"""NewsEventStrategy - multi-day event-driven strategy running alongside DailyRSIStrategy.

Signal source: LLM-extracted event_type + severity per headline (via news_event_extractor).
Instrument routing: severity ≥ NEWS_STRATEGY_SEVERITY_MIN_OPTIONS -> ATM call option,
                    severity in [NEWS_STRATEGY_SEVERITY_MIN_TO_ENTER, that) -> stock.
Position lifecycle: pending -> open -> closed, managed by position_manager (Phase 3).
Exit priority: stop > target > reversal > time.

Coexists with DailyRSIStrategy:
  * NewsEventStrategy positions live in the multi_day_positions table
  * DailyRSIStrategy positions live in its own dict (unchanged)
  * Both share regime_gate + risk_gate + audit log
  * Concurrent-slot limits are per-strategy (NEWS_STRATEGY_MAX_CONCURRENT vs MAX_POSITIONS)

Feature flag: settings.NEWS_STRATEGY_ENABLED (default False). Off means the
strategy still evaluates exits on any lingering open positions but does not
open new ones - safe way to observe signal without new risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.logger import logger
from app.services.ai_guardrails import sanitize_headlines
from app.services.alpaca_client import alpaca_client
from app.services.news_event_extractor import (
    AggregateSignal,
    ExtractedEvent,
    news_event_extractor,
)
from app.services.news_feed import news_feed
from app.services.position_manager import (
    EXIT_ERROR,
    INSTRUMENT_OPTION,
    INSTRUMENT_STOCK,
    ExitDecision,
    Position,
    position_manager,
)
from app.services.regime import regime_gate
from app.services.risk_gate import risk_gate
from app.services.utils import record_audit_entry
from app.services.watchlist_manager import EXPERT_PICKS, get_atm_option_symbols


STRATEGY_NAME = "news_event_v1"


@dataclass(frozen=True)
class EntryCandidate:
    symbol: str
    underlying: str
    instrument: str
    aggregate_signal: AggregateSignal
    events_used: int
    top_event_type: str


# Underscore-prefix: OCC option symbols use O:{ROOT}...; underlying is everything
# between "O:" and the first digit (YYMMDD). Falls back to stripped symbol.
def _underlying_of(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("O:"):
        core = s[2:]
        # Find where numerics start (YYMMDD)
        for i, ch in enumerate(core):
            if ch.isdigit():
                return core[:i]
        return core
    return s.split(".")[0]


class NewsEventStrategy:
    """Coordinator: pull news -> extract events -> aggregate -> gate -> position_manager."""

    def __init__(self) -> None:
        self.last_run_date: Optional[date] = None

    # ── Public API ─────────────────────────────────────────────────────────

    def universe(self) -> List[str]:
        """Symbols the strategy evaluates each pass. Uses EXPERT_PICKS for parity
        with the RSI universe (24 curated names). Overrideable via settings later."""
        return list(EXPERT_PICKS.keys())

    def scan_for_entries(self) -> List[EntryCandidate]:
        """Score each universe symbol, aggregate severity, return actionable candidates.

        Skips symbols with no news, no signal, or aggregate below the entry threshold.
        Sorted by |aggregate| descending so the strongest signals get slot priority.
        """
        min_enter = float(settings.NEWS_STRATEGY_SEVERITY_MIN_TO_ENTER)
        min_options = float(settings.NEWS_STRATEGY_SEVERITY_MIN_OPTIONS)
        max_per_call = int(settings.NEWS_EVENT_MAX_HEADLINES_PER_CALL)
        candidates: List[EntryCandidate] = []

        for sym in self.universe():
            try:
                headlines = news_feed.headlines(sym) or []
            except Exception as e:
                logger.debug(f"[news_strategy] news_feed failed for {sym}: {e}")
                continue
            if not headlines:
                continue

            # sanitize + cap; extractor also sanitizes but we cap here so LLM cost
            # matches the config surface (NEWS_EVENT_MAX_HEADLINES_PER_CALL)
            headlines = sanitize_headlines(headlines, max_per_call, settings.AI_MAX_HEADLINE_CHARS)
            if not headlines:
                continue

            events: List[ExtractedEvent] = news_event_extractor.extract(sym, headlines)
            agg = news_event_extractor.aggregate_severity(events)
            if agg is None or agg.n_events == 0:
                continue

            # Strategy uses SIGNED aggregate for entry decisions:
            #   +positive & >= min_enter -> potential long
            #   -negative & <= -min_enter -> skip (no shorting in v1)
            if agg.aggregate < min_enter:
                continue

            # Instrument routing on |aggregate| for symmetry, but strategy only
            # enters long today. Sign check above already filters shorts.
            if agg.aggregate >= min_options:
                instrument = INSTRUMENT_OPTION
                # Resolve OCC ATM call symbol; on failure, fall back to stock.
                opt_symbols = get_atm_option_symbols(sym, weeks_out=4)
                traded_symbol = opt_symbols[0] if opt_symbols else sym
                if not opt_symbols:
                    logger.info(f"[news_strategy] {sym} options unavailable, downgrading to stock")
                    instrument = INSTRUMENT_STOCK
            else:
                instrument = INSTRUMENT_STOCK
                traded_symbol = sym

            candidates.append(EntryCandidate(
                symbol=traded_symbol,
                underlying=sym,
                instrument=instrument,
                aggregate_signal=agg,
                events_used=agg.n_events,
                top_event_type=agg.top_event_type or "other",
            ))

        # Strongest signal first; slot allocation walks this list.
        candidates.sort(key=lambda c: -abs(c.aggregate_signal.aggregate))
        return candidates

    def evaluate_pass(self) -> Dict[str, int]:
        """One full pass: exits first, then entries. Returns counters for logging.

        Called by start_trading_loop after regime + risk gates are evaluated.
        Idempotent-ish - safe to call multiple times per day; entries are
        deduplicated by can_open_new_position() and per-underlying position count.
        """
        counters = {
            "candidates": 0,
            "entries_opened": 0,
            "entries_blocked_regime": 0,
            "entries_blocked_risk": 0,
            "entries_blocked_slot": 0,
            "entries_blocked_dup": 0,
            "exits_evaluated": 0,
            "exits_executed": 0,
            "exits_failed": 0,
        }

        # ── 1. EXITS - always evaluate, even when strategy is disabled ─────
        # If the strategy is off but has open positions from a prior enabled
        # run, we still want to manage them. This is a safety property, not
        # a feature: never let a disabled flag orphan real positions.
        exits = self._evaluate_exits_with_quotes()
        counters["exits_evaluated"] = len(exits)
        for decision in exits:
            ok = self._execute_exit(decision)
            counters["exits_executed" if ok else "exits_failed"] += 1

        # ── 2. ENTRIES - respect feature flag ──────────────────────────────
        if not settings.NEWS_STRATEGY_ENABLED:
            return counters

        candidates = self.scan_for_entries()
        counters["candidates"] = len(candidates)
        if not candidates:
            return counters

        # Regime gate: single global check per pass (mirrors RSI wiring).
        regime = regime_gate.classify()
        if not regime.allow_new_buys:
            logger.info(
                f"[news_strategy] entries skipped this pass: regime={regime.regime} ({regime.reason})"
            )
            counters["entries_blocked_regime"] = len(candidates)
            return counters

        for cand in candidates:
            if not position_manager.can_open_new_position(strategy=STRATEGY_NAME):
                counters["entries_blocked_slot"] += 1
                continue
            # Only one open position per underlying at a time (avoid stacking on
            # the same catalyst).
            existing = position_manager.list_positions(
                strategy=STRATEGY_NAME, underlying=cand.underlying,
            )
            if any(p.state != "closed" for p in existing):
                counters["entries_blocked_dup"] += 1
                continue

            entered = self._enter_position(cand)
            if entered is None:
                counters["entries_blocked_risk"] += 1
            else:
                counters["entries_opened"] += 1

        return counters

    def snapshot(self) -> dict:
        return {
            "strategy": STRATEGY_NAME,
            "enabled": settings.NEWS_STRATEGY_ENABLED,
            "min_severity_to_enter": settings.NEWS_STRATEGY_SEVERITY_MIN_TO_ENTER,
            "min_severity_for_options": settings.NEWS_STRATEGY_SEVERITY_MIN_OPTIONS,
            "hold_days": settings.NEWS_STRATEGY_HOLD_DAYS,
            "stop_pct": settings.NEWS_STRATEGY_STOP_LOSS_PCT,
            "target_pct": settings.NEWS_STRATEGY_TAKE_PROFIT_PCT,
            "reversal_multiplier": settings.NEWS_STRATEGY_REVERSAL_SEVERITY_MULT,
            "position_dollars": settings.NEWS_STRATEGY_POSITION_DOLLARS,
            "max_concurrent": settings.NEWS_STRATEGY_MAX_CONCURRENT,
            "positions": position_manager.snapshot(),
        }

    # ── Internals ──────────────────────────────────────────────────────────

    def _enter_position(self, cand: EntryCandidate) -> Optional[Position]:
        """Size, risk-check, submit BUY, record pending position."""
        # Fetch current quote for sizing and risk gate.
        try:
            quote = alpaca_client.get_quote(cand.symbol)
        except Exception as e:
            logger.warning(f"[news_strategy] quote failed for {cand.symbol}: {e}")
            return None
        # Prefer ask for entry sizing — that's what we'd actually pay. pLast can
        # be very stale for thinly-traded options and would under-size notional
        # against the risk gate's concentration/daily-loss checks.
        if not quote:
            logger.info(f"[news_strategy] no quote for {cand.symbol}, skipping entry")
            return None
        price = quote.get("ask") or quote.get("pLast")
        if not price or price <= 0:
            logger.info(f"[news_strategy] no usable quote for {cand.symbol}, skipping entry")
            return None
        price = float(price)

        # Sizing: fixed notional / price -> whole shares. For options, 1 contract
        # is sized directly from settings (options are lumpy - a single ATM
        # call typically ~$100-500 premium and represents 100 shares of exposure).
        if cand.instrument == INSTRUMENT_OPTION:
            qty = 1
            estimated_notional = price * 100  # option contract is x100
        else:
            target_notional = float(settings.NEWS_STRATEGY_POSITION_DOLLARS)
            qty = max(1, int(target_notional // price))
            estimated_notional = qty * price

        # Risk gate: uses the underlying for concentration.
        risk = risk_gate.evaluate(cand.underlying, "BUY", estimated_notional)
        if not risk.approved:
            logger.info(
                f"[news_strategy] BUY {cand.symbol} blocked by risk gate: {risk.reason}"
            )
            return None

        # Submit BUY order. record_audit_entry captures the intent for the
        # legacy trade_audit trail so external dashboards see the trade.
        try:
            order = alpaca_client.place_order(
                symbol=cand.symbol, quantity=qty, side="buy", order_type="market",
            )
        except Exception as e:
            logger.error(f"[news_strategy] place_order failed for {cand.symbol}: {e}")
            record_audit_entry({
                "strategy": STRATEGY_NAME,
                "symbol": cand.symbol,
                "underlying": cand.underlying,
                "side": "buy",
                "quantity": qty,
                "instrument": cand.instrument,
                "status": "rejected",
                "error": str(e)[:200],
                "aggregate_severity": cand.aggregate_signal.aggregate,
            })
            return None

        pos = position_manager.open_position(
            strategy=STRATEGY_NAME,
            symbol=cand.symbol,
            underlying=cand.underlying,
            instrument=cand.instrument,
            entry_severity=cand.aggregate_signal.aggregate,
            entry_event_type=cand.top_event_type,
            entry_reason=(
                f"sev={cand.aggregate_signal.aggregate:.1f} "
                f"top={cand.top_event_type} n={cand.events_used}"
            ),
            entry_order_id=(order or {}).get("order_id") if isinstance(order, dict) else None,
            payload={
                "estimated_notional": estimated_notional,
                "quote_at_signal": price,
                "instrument": cand.instrument,
            },
        )

        record_audit_entry({
            "strategy": STRATEGY_NAME,
            "symbol": cand.symbol,
            "underlying": cand.underlying,
            "side": "buy",
            "quantity": qty,
            "instrument": cand.instrument,
            "status": "submitted",
            "position_id": pos.id,
            "aggregate_severity": cand.aggregate_signal.aggregate,
            "top_event_type": cand.top_event_type,
        })

        # NOTE: fill isn't confirmed here. The next loop iteration should
        # reconcile pending positions with alpaca_client.get_open_orders() and
        # call position_manager.record_fill() when filled. That reconciliation
        # is a Phase 4b loop-wiring concern.
        logger.info(
            f"[news_strategy] entered {cand.symbol} ({cand.instrument}) "
            f"qty={qty} sev={cand.aggregate_signal.aggregate:.1f} "
            f"top={cand.top_event_type} pos_id={pos.id}"
        )
        return pos

    def _evaluate_exits_with_quotes(self) -> List[ExitDecision]:
        """Fetch current quotes for all open positions in one batched call,
        then delegate to position_manager.evaluate_exits()."""
        open_positions = position_manager.list_positions(
            strategy=STRATEGY_NAME, state="open",
        )
        if not open_positions:
            return []
        # Batch quote lookup for efficiency.
        symbols = sorted({p.symbol for p in open_positions})
        try:
            quotes = alpaca_client.get_batch_quotes(symbols) or {}
        except Exception as e:
            logger.warning(f"[news_strategy] batch quotes failed: {e}")
            quotes = {}
        # Normalize quote dict to symbol -> mid price.
        prices: Dict[str, float] = {}
        for sym, q in quotes.items():
            if not q:
                continue
            p = q.get("pLast") or q.get("last") or (
                (q.get("bid", 0) + q.get("ask", 0)) / 2.0 if q.get("bid") and q.get("ask") else None
            )
            if p and p > 0:
                prices[sym] = float(p)
        return position_manager.evaluate_exits(prices)

    def _execute_exit(self, decision: ExitDecision) -> bool:
        """Submit SELL for a position and mark it closed on success."""
        try:
            order = alpaca_client.place_order(
                symbol=decision.symbol, quantity=decision.shares,
                side="sell", order_type="market",
            )
        except Exception as e:
            logger.error(
                f"[news_strategy] exit place_order failed for id={decision.position_id} "
                f"{decision.symbol}: {e}"
            )
            record_audit_entry({
                "strategy": STRATEGY_NAME,
                "symbol": decision.symbol,
                "underlying": decision.underlying,
                "side": "sell",
                "quantity": decision.shares,
                "instrument": decision.instrument,
                "status": "rejected",
                "position_id": decision.position_id,
                "error": str(e)[:200],
                "exit_reason": decision.exit_reason,
            })
            return False

        # Close at the estimated exit price; real fill reconciliation is a
        # Phase 4b loop concern - if the actual fill diverges materially, an
        # accounting job will update realized_pnl. For now the estimate is
        # good enough for state-machine correctness.
        position_manager.close_position(
            decision.position_id,
            exit_price=decision.exit_price_estimate,
            exit_reason=decision.exit_reason,
            broker_order_id=(order or {}).get("order_id") if isinstance(order, dict) else None,
        )
        record_audit_entry({
            "strategy": STRATEGY_NAME,
            "symbol": decision.symbol,
            "underlying": decision.underlying,
            "side": "sell",
            "quantity": decision.shares,
            "instrument": decision.instrument,
            "status": "submitted",
            "position_id": decision.position_id,
            "exit_reason": decision.exit_reason,
        })
        return True


news_event_strategy = NewsEventStrategy()
