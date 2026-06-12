"""
Pre-trade Risk Gate — runs AFTER the AI advisor approves and BEFORE order placement.

The AI advisor decides whether a signal is *technically* worth taking. The risk
gate decides whether the *portfolio* can afford to take it. These are different
questions and must be checked separately.

Checks (each configurable via settings.RISK_*):

  • Concentration   — would this new position push the symbol above
                      RISK_MAX_SYMBOL_CONCENTRATION_PCT of account equity?
  • Daily loss      — has today's realized + unrealized P&L fallen below
                      -RISK_MAX_DAILY_LOSS_DOLLARS?
  • Drawdown        — is account equity more than RISK_MAX_DRAWDOWN_PCT below
                      the rolling RISK_PEAK_EQUITY_WINDOW_DAYS peak?

Every evaluation is persisted to the `risk_events` table for audit. The gate
also opportunistically writes account snapshots so the drawdown peak stays
fresh — no separate cron needed.

The gate FAIL-OPENS on broker errors: if we cannot fetch equity / positions,
we log loudly but allow the trade through. Rationale: a broker-side outage
is independent of risk posture, and stopping all trading on a transient
quotes failure would be over-eager. The reliability circuit breaker on
alpaca_client (if added later) is the right tool to gate on broker health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logger import logger
from app.services.alpaca_client import alpaca_client
from app.services.pnl import realized_pnl_today
from app.services.utils import (
    get_peak_equity,
    record_account_snapshot,
    record_risk_event,
)


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    breaches: List[str] = field(default_factory=list)
    snapshot: Dict = field(default_factory=dict)


class RiskGate:
    """Stateless evaluator — all state lives in SQLite + the broker."""

    def evaluate(
        self,
        symbol: str,
        proposed_action: str,
        proposed_notional: float,
    ) -> RiskDecision:
        """Return RiskDecision for a single proposed trade.

        proposed_notional is the dollar value the trade will add to the symbol's
        exposure (option premium × contract size × 100 for options, or share
        count × price for stocks). The caller computes this — the gate does not
        know about contract multipliers.
        """
        symbol = symbol.upper()

        if not settings.RISK_GATE_ENABLED:
            decision = RiskDecision(approved=True, reason="risk gate disabled")
            self._persist(symbol, proposed_action, decision)
            return decision

        # Pull live state. Fail-open if the broker is down.
        account = alpaca_client.get_account_pnl()
        positions = alpaca_client.get_positions_pnl() or []

        if account is None:
            decision = RiskDecision(
                approved=True,
                reason="broker unavailable — fail-open (alert and proceed)",
                snapshot={"account": None, "positions_count": len(positions)},
            )
            logger.warning(f"[RISK] {symbol} broker unreachable, allowing {proposed_action}")
            self._persist(symbol, proposed_action, decision)
            return decision

        equity = float(account.get("equity") or 0.0)
        day_pl = float(account.get("day_pl") or 0.0)
        unrealized_pl = float(account.get("unrealized_pl") or 0.0)

        # Refresh peak-equity tracking before the drawdown check reads it.
        try:
            record_account_snapshot(
                equity=equity,
                cash=account.get("cash"),
                buying_power=account.get("buying_power"),
                payload=account,
            )
        except Exception as e:
            logger.debug(f"[RISK] snapshot persist failed: {e}")

        snapshot = {
            "equity": equity,
            "day_pl": day_pl,
            "unrealized_pl": unrealized_pl,
            "open_positions": len(positions),
            "proposed_notional": proposed_notional,
        }

        breaches: List[str] = []
        is_buy = proposed_action.upper() == "BUY"

        # 1. Concentration (only relevant for BUYs — exits reduce concentration)
        if is_buy and equity > 0:
            current_symbol_value = sum(
                float(p.get("market_value") or 0.0)
                for p in positions
                if _underlying(p.get("symbol", "")) == symbol
                   or p.get("symbol") == symbol
            )
            future_value = current_symbol_value + max(0.0, proposed_notional)
            future_pct = future_value / equity * 100
            snapshot["symbol_current_value"] = current_symbol_value
            snapshot["symbol_future_pct"] = round(future_pct, 2)
            snapshot["concentration_cap_pct"] = settings.RISK_MAX_SYMBOL_CONCENTRATION_PCT
            if future_pct > settings.RISK_MAX_SYMBOL_CONCENTRATION_PCT:
                breaches.append(
                    f"concentration: {future_pct:.1f}% > {settings.RISK_MAX_SYMBOL_CONCENTRATION_PCT:.1f}% cap"
                )

        # 2. Daily loss halt — Alpaca's day_pl already reflects realized + unrealized
        # at the account level, but it resets at UTC midnight on Alpaca's clock and
        # can be stale on transient API hiccups. We add our own FIFO realized P&L
        # from trade_audit as a cross-check and take the more pessimistic number.
        try:
            audit_realized = realized_pnl_today()
        except Exception:
            audit_realized = 0.0
        snapshot["audit_realized_today"] = audit_realized
        total_day = day_pl + unrealized_pl
        # Pessimistic floor: whichever calculation shows MORE loss wins.
        worst_case_day = min(total_day, audit_realized + unrealized_pl)
        snapshot["total_day_pl"] = round(worst_case_day, 2)
        snapshot["daily_loss_floor"] = -settings.RISK_MAX_DAILY_LOSS_DOLLARS
        if worst_case_day < -settings.RISK_MAX_DAILY_LOSS_DOLLARS:
            if is_buy or settings.RISK_HALT_BLOCKS_SELLS:
                breaches.append(
                    f"daily_loss: ${worst_case_day:.2f} < -${settings.RISK_MAX_DAILY_LOSS_DOLLARS:.0f}"
                )

        # 3. Drawdown vs rolling peak
        peak = get_peak_equity(settings.RISK_PEAK_EQUITY_WINDOW_DAYS) or equity
        snapshot["peak_equity"] = peak
        snapshot["drawdown_pct"] = round((peak - equity) / peak * 100, 2) if peak else 0.0
        snapshot["drawdown_cap_pct"] = settings.RISK_MAX_DRAWDOWN_PCT
        if peak > 0 and snapshot["drawdown_pct"] > settings.RISK_MAX_DRAWDOWN_PCT:
            if is_buy or settings.RISK_HALT_BLOCKS_SELLS:
                breaches.append(
                    f"drawdown: {snapshot['drawdown_pct']:.1f}% > {settings.RISK_MAX_DRAWDOWN_PCT:.1f}% cap"
                )

        approved = not breaches
        reason = "approved" if approved else "; ".join(breaches)
        decision = RiskDecision(
            approved=approved, reason=reason, breaches=breaches, snapshot=snapshot
        )

        log = logger.info if approved else logger.warning
        log(
            f"[RISK] {symbol} {proposed_action} {'APPROVED' if approved else 'BLOCKED'}"
            + (f" — {reason}" if not approved else "")
        )

        self._persist(symbol, proposed_action, decision)
        return decision

    def snapshot(self) -> Dict:
        """Current risk posture for the /trades/risk-status endpoint."""
        account = alpaca_client.get_account_pnl()
        positions = alpaca_client.get_positions_pnl() or []
        equity = float(account.get("equity") or 0.0) if account else 0.0
        day_pl = float(account.get("day_pl") or 0.0) if account else 0.0
        unrealized = float(account.get("unrealized_pl") or 0.0) if account else 0.0
        peak = get_peak_equity(settings.RISK_PEAK_EQUITY_WINDOW_DAYS)
        drawdown_pct = round((peak - equity) / peak * 100, 2) if peak and equity else 0.0

        concentrations = {}
        for p in positions:
            sym = _underlying(p.get("symbol", "")) or p.get("symbol", "")
            mv = float(p.get("market_value") or 0.0)
            concentrations[sym] = concentrations.get(sym, 0.0) + mv
        concentration_pct = {
            sym: round(mv / equity * 100, 2) if equity else 0.0
            for sym, mv in concentrations.items()
        }

        return {
            "enabled": settings.RISK_GATE_ENABLED,
            "broker_available": account is not None,
            "equity": equity,
            "day_pl": round(day_pl, 2),
            "unrealized_pl": round(unrealized, 2),
            "peak_equity": peak,
            "drawdown_pct": drawdown_pct,
            "limits": {
                "concentration_pct": settings.RISK_MAX_SYMBOL_CONCENTRATION_PCT,
                "daily_loss_dollars": settings.RISK_MAX_DAILY_LOSS_DOLLARS,
                "drawdown_pct": settings.RISK_MAX_DRAWDOWN_PCT,
                "peak_window_days": settings.RISK_PEAK_EQUITY_WINDOW_DAYS,
            },
            "concentration_by_symbol_pct": concentration_pct,
        }

    @staticmethod
    def _persist(symbol: str, proposed_action: str, decision: RiskDecision) -> None:
        try:
            record_risk_event({
                "symbol": symbol,
                "proposed_action": proposed_action,
                "approved": decision.approved,
                "breaches": decision.breaches,
                "reason": decision.reason,
                "snapshot": decision.snapshot,
            })
        except Exception as e:
            logger.error(f"[RISK] audit persist failed: {e}")


def _underlying(opt_or_stock_symbol: str) -> Optional[str]:
    """Extract the underlying ticker from an OCC option symbol, or return the stock symbol as-is.

    OCC format: O:AAPL250117C00185000 → AAPL
    Bare ticker: AAPL → AAPL
    """
    if not opt_or_stock_symbol:
        return None
    s = opt_or_stock_symbol.upper()
    if s.startswith("O:"):
        s = s[2:]
    # OCC body: TICKER + YYMMDD + C/P + STRIKE
    # Walk back from the first digit to find the ticker boundary.
    for i, ch in enumerate(s):
        if ch.isdigit():
            return s[:i] if i > 0 else None
    return s


risk_gate = RiskGate()
