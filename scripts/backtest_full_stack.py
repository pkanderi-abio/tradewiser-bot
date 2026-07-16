"""Full-stack backtest — replays historical signals through
Regime → AI → Risk gates, so you can measure what each gate actually does.

What this answers:
  - Which gate blocks how many signals (calibration)
  - What's the counterfactual P&L on blocked signals — is the gate earning
    its keep or leaving money on the table?
  - Combined equity curve of "raw signal" vs "gated signal"

Deliberate simplifications (document these in every report):
  1. P&L is modeled on the STOCK underlying, not the option. We're
     measuring signal quality × gate quality, not option-strategy P&L.
     That would need IV-surface modeling and is a different backtest.
  2. Fills happen at next-day OPEN. No slippage, no partials.
  3. Position sizing is fixed dollars per trade (settings.TRADING_MAX_POSITION_SIZE).

Usage:
    python -m scripts.backtest_full_stack \
        --start 2025-01-01 --end 2025-06-30 \
        --symbols AAPL,SPY,QQQ \
        --live-ai            # call the real advisor (uses cache)
        --out-dir runs

    # or fully offline:
    python -m scripts.backtest_full_stack ... --stub-ai
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

import pandas as pd
import yfinance as yf

from app.services.backtest_cache import (
    AIDecisionCache,
    CachedDecision,
    make_cache_key,
)
from app.services.market_data import MarketSnapshot
from app.services.regime import regime_gate
from app.services.risk_gate import risk_gate
from app.services.trading_engine import (
    RSI_BUY_THRESHOLD,
    RSI_SELL_THRESHOLD,
    _compute_rsi,
)


# ── Historical macro (VIX + SPY) ──────────────────────────────────────────────

def load_macro_history(start: str, end: str) -> pd.DataFrame:
    """Return a DataFrame indexed by date with columns:
       vix, vix_prev, spy_close, spy_sma50, spy_sma200, spy_trend

    trend classification mirrors app.services.market_data:
        uptrend: spy > sma50 > sma200
        downtrend: spy < sma50 < sma200
        chop: everything else
    """
    # Pad the window so SMA200 has enough lookback on day 1.
    pad_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")

    vix = yf.download("^VIX", start=pad_start, end=end, progress=False, auto_adjust=False)
    spy = yf.download("SPY",  start=pad_start, end=end, progress=False, auto_adjust=False)
    if vix.empty or spy.empty:
        raise RuntimeError("yfinance returned no macro data — retry later")

    # yfinance may return a MultiIndex on columns for single tickers; flatten it.
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = vix.columns.get_level_values(0)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)

    df = pd.DataFrame(index=spy.index)
    df["spy_close"] = spy["Close"]
    df["spy_sma50"] = spy["Close"].rolling(50).mean()
    df["spy_sma200"] = spy["Close"].rolling(200).mean()
    df["vix"] = vix["Close"].reindex(df.index).ffill()
    df["vix_prev"] = df["vix"].shift(1)

    def _classify(row) -> Optional[str]:
        s, m50, m200 = row["spy_close"], row["spy_sma50"], row["spy_sma200"]
        if pd.isna(m50) or pd.isna(m200) or pd.isna(s):
            return None
        if s > m50 > m200:
            return "uptrend"
        if s < m50 < m200:
            return "downtrend"
        return "chop"

    df["spy_trend"] = df.apply(_classify, axis=1)
    return df.loc[start:end]


def market_snapshot_for_date(macro: pd.DataFrame, d: pd.Timestamp) -> MarketSnapshot:
    """Build the same MarketSnapshot the live regime gate consumes.

    We only fill the fields RegimeGate.classify() reads. Others stay None.
    """
    if d not in macro.index:
        # Weekend / holiday — regime gate treats missing data as fail-open.
        return MarketSnapshot(
            vix=None, vix_pct_change=None,
            spy_price=None, spy_sma50=None, spy_sma200=None,
            spy_trend=None, spy_distance_to_sma50_pct=None,
            qqq_price=None, qqq_trend=None,
            fetched_at=time.time(),
        )
    row = macro.loc[d]
    vix = float(row["vix"]) if pd.notna(row["vix"]) else None
    prev = float(row["vix_prev"]) if pd.notna(row["vix_prev"]) else None
    vix_pct = ((vix - prev) / prev * 100) if (vix is not None and prev) else None
    return MarketSnapshot(
        vix=vix,
        vix_pct_change=vix_pct,
        spy_price=float(row["spy_close"]) if pd.notna(row["spy_close"]) else None,
        spy_sma50=float(row["spy_sma50"]) if pd.notna(row["spy_sma50"]) else None,
        spy_sma200=float(row["spy_sma200"]) if pd.notna(row["spy_sma200"]) else None,
        spy_trend=row["spy_trend"] if pd.notna(row["spy_trend"]) else None,
        spy_distance_to_sma50_pct=None,
        qqq_price=None,
        qqq_trend=None,
        fetched_at=time.time(),
    )


# ── Synthetic portfolio state served to the risk gate ────────────────────────

@dataclass
class Position:
    symbol: str
    qty: int
    avg_entry: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price


@dataclass
class SyntheticBook:
    """Fake broker state that answers `get_account_pnl` / `get_positions_pnl`
    the same way Alpaca does. The risk gate reads these two calls only —
    we don't need to fake the whole trading_client surface."""

    starting_equity: float = 100_000.0
    cash: float = 100_000.0
    realized_day_pl: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)

    def account_pnl(self) -> dict:
        unrealized = sum(
            (p.current_price - p.avg_entry) * p.qty for p in self.positions.values()
        )
        equity = self.cash + sum(p.market_value for p in self.positions.values())
        return {
            "equity": equity,
            "cash": self.cash,
            "portfolio_value": equity,
            "buying_power": self.cash,
            "unrealized_pl": unrealized,
            "unrealized_plpc": 0.0,
            "last_equity": equity - self.realized_day_pl,
            "day_pl": self.realized_day_pl,
            "day_plpc": 0.0,
        }

    def positions_pnl(self) -> list:
        return [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "side": "long",
                "asset_class": "us_equity",
                "avg_entry_price": p.avg_entry,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "cost_basis": p.avg_entry * p.qty,
                "unrealized_pl": (p.current_price - p.avg_entry) * p.qty,
                "unrealized_plpc": 0.0,
            }
            for p in self.positions.values()
        ]

    def mark_to_market(self, prices_for_day: Dict[str, float]) -> None:
        for sym, pos in self.positions.items():
            if sym in prices_for_day and pd.notna(prices_for_day[sym]):
                pos.current_price = float(prices_for_day[sym])

    def open_position(self, sym: str, qty: int, price: float) -> None:
        if sym in self.positions:
            existing = self.positions[sym]
            new_qty = existing.qty + qty
            new_avg = ((existing.avg_entry * existing.qty) + (price * qty)) / new_qty
            existing.qty, existing.avg_entry, existing.current_price = new_qty, new_avg, price
        else:
            self.positions[sym] = Position(sym, qty, price, price)
        self.cash -= qty * price

    def close_position(self, sym: str, price: float) -> float:
        """Close full position, return realized P&L."""
        pos = self.positions.pop(sym, None)
        if not pos:
            return 0.0
        pl = (price - pos.avg_entry) * pos.qty
        self.cash += pos.qty * price
        self.realized_day_pl += pl
        return pl

    def new_day(self) -> None:
        self.realized_day_pl = 0.0


# ── AI advisor callable — either the real one, a stub, or cache-only ─────────

@dataclass
class Signal:
    symbol: str
    action: str            # BUY / SELL
    price: float           # signal-day close
    rsi: float
    price_history: List[float]
    date: pd.Timestamp


AiCallable = Callable[[Signal], CachedDecision]


# Legacy reason-substring markers used by --purge-fail-closed to clean up
# poisoned rows that predate the outcome field. New rows can't get poisoned
# (AIDecisionCache.put refuses fail-closed decisions), so this list only
# needs to cover what historically leaked through — no need to keep it in
# sync with future advisor changes.
_LEGACY_FAIL_CLOSED_REASON_MARKERS = (
    "circuit breaker open",
    "kill_switch",
    "LLM unavailable",
    "LLM llm_error",
    "LLM timeout",
    "LLM schema_error",
)


def reset_advisor_circuit() -> None:
    """Force the live ai_advisor's breaker back to CLOSED before a backtest.

    The advisor is a module-level singleton — its circuit state carries over
    between runs (and worse: if a prior backtest tripped it, the next one
    starts by seeing every call short-circuited to HOLD). We reset here so
    each backtest run starts from a clean slate, and we log the pre-reset
    state so operators can see if the previous run stressed the provider.
    """
    try:
        from app.services.ai_advisor import ai_advisor
        breaker = getattr(ai_advisor, "_breaker", None)
        if breaker is None:
            return
        pre = breaker.snapshot()
        with breaker._lock:                    # noqa: SLF001 — reset internals
            breaker._consecutive_failures = 0
            breaker._opened_at = None
        if pre.get("state") != "closed":
            print(f"[backtest] reset AI circuit breaker (was {pre})")
    except Exception as e:
        print(f"[backtest] could not reset circuit breaker: {e}")


def make_ai_callable(
    *,
    live: bool,
    cache: AIDecisionCache,
    provider_hint: str = "backtest",
    model_hint: str = "backtest",
    per_call_delay_s: float = 0.0,
) -> AiCallable:
    """Build the AI decision function used by the backtest.

    - live=True: import the real advisor, call decide(), cache the result.
    - live=False: cache-only (miss → deterministic pass-through: signal
      approved at confidence 0.7). Used for offline runs / smoke tests.

    per_call_delay_s throttles live calls to keep us under provider rate
    limits and avoid tripping the ai_advisor's circuit breaker mid-run.
    """
    if live:
        from app.services.ai_advisor import ai_advisor

        provider = ai_advisor.get_provider() if hasattr(ai_advisor, "get_provider") else provider_hint
        model = ai_advisor.get_model() if hasattr(ai_advisor, "get_model") else model_hint

        def _live(sig: Signal) -> CachedDecision:
            key = make_cache_key(
                symbol=sig.symbol, date_str=sig.date.strftime("%Y-%m-%d"),
                proposed_action=sig.action, price=sig.price, momentum=sig.rsi,
                price_history=sig.price_history,
                provider=provider, model=model,
            )
            hit = cache.get(key)
            if hit is not None:
                return hit
            if per_call_delay_s > 0:
                time.sleep(per_call_delay_s)
            decision = ai_advisor.decide(
                symbol=sig.symbol, price=sig.price, momentum=sig.rsi,
                price_history=sig.price_history, position=0,
                proposed_action=sig.action,
            )
            cd = CachedDecision(
                action=decision.get("action", "HOLD"),
                confidence=float(decision.get("confidence", 0.0)),
                reason=decision.get("reason", ""),
                outcome=decision.get("outcome", "ok"),
            )
            # AIDecisionCache.put refuses fail-closed decisions on its own —
            # this call is a no-op when the advisor short-circuited (breaker,
            # kill switch, LLM error). The wrapper still returns the HOLD to
            # the caller so downstream backtest logic interprets the day
            # correctly; the next re-run will re-ask against a healthy provider.
            cache.put(
                key, symbol=sig.symbol,
                date_str=sig.date.strftime("%Y-%m-%d"),
                decision=cd, provider=provider, model=model,
            )
            return cd

        return _live

    # Offline stub — cache still used so re-runs are still deterministic.
    def _stub(sig: Signal) -> CachedDecision:
        key = make_cache_key(
            symbol=sig.symbol, date_str=sig.date.strftime("%Y-%m-%d"),
            proposed_action=sig.action, price=sig.price, momentum=sig.rsi,
            price_history=sig.price_history,
            provider=provider_hint, model=model_hint,
        )
        hit = cache.get(key)
        if hit is not None:
            return hit
        cd = CachedDecision(action=sig.action, confidence=0.7, reason="stub advisor")
        cache.put(
            key, symbol=sig.symbol,
            date_str=sig.date.strftime("%Y-%m-%d"),
            decision=cd, provider=provider_hint, model=model_hint,
        )
        return cd

    return _stub


# ── Runner ───────────────────────────────────────────────────────────────────

@dataclass
class GateHit:
    date: str
    symbol: str
    action: str          # what the strategy wanted
    stage: str           # regime / ai / risk / taken
    outcome: str         # allowed / blocked_regime / blocked_ai / blocked_risk / no_signal
    reason: str


@dataclass
class RunResult:
    hits: List[GateHit]
    blocked_counterfactual: List[dict]      # what would blocked trades have made
    taken_pnl: List[dict]                   # realized P&L for taken trades
    equity_curve: List[dict]


def _rolling_signals(hist: pd.DataFrame, sym: str, d: pd.Timestamp) -> Optional[Signal]:
    """Return a Signal if RSI-based BUY/SELL fires on day d, else None."""
    upto = hist.loc[:d]
    if len(upto) < 60:
        return None
    rsi = _compute_rsi(upto["Close"], period=14)
    if pd.isna(rsi):
        return None
    price = float(upto["Close"].iloc[-1])
    price_history = upto["Close"].tail(60).tolist()
    if rsi < RSI_BUY_THRESHOLD:
        return Signal(sym, "BUY", price, rsi, price_history, d)
    if rsi > RSI_SELL_THRESHOLD:
        return Signal(sym, "SELL", price, rsi, price_history, d)
    return None


def _next_open(hist: pd.DataFrame, d: pd.Timestamp) -> Optional[Tuple[pd.Timestamp, float]]:
    """Return (next-trading-day, next-open) or None if we're at series end."""
    after = hist.loc[hist.index > d]
    if after.empty:
        return None
    row = after.iloc[0]
    return after.index[0], float(row["Open"])


def run_backtest(
    *,
    start: str,
    end: str,
    symbols: List[str],
    ai_call: AiCallable,
    book: SyntheticBook,
    trade_dollars: float = 1000.0,
    stop_loss_pct: float = 0.03,       # 3% underlying stop — deliberately tight because options magnify
    profit_target_pct: float = 0.05,   # 5% underlying target
    ai_min_confidence: float = 0.65,
    hist_source: Optional[Callable[[str], pd.DataFrame]] = None,
    macro_override: Optional[pd.DataFrame] = None,
) -> RunResult:
    """Deterministic backtest given historical bars + AI decision callable.

    hist_source lets tests inject synthetic OHLCV without hitting yfinance.
    macro_override does the same for VIX/SPY.
    """
    if hist_source is None:
        pad_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
        hist_source = lambda s: _download_ohlcv(s, pad_start, end)

    histories: Dict[str, pd.DataFrame] = {s: hist_source(s) for s in symbols}
    macro = macro_override if macro_override is not None else load_macro_history(start, end)

    hits: List[GateHit] = []
    blocked_cf: List[dict] = []
    taken: List[dict] = []
    equity_curve: List[dict] = []

    # Master trading calendar = union of all symbol dates in [start, end].
    all_dates = sorted(set().union(
        *[list(h.loc[start:end].index) for h in histories.values() if not h.empty]
    ))

    for d in all_dates:
        book.new_day()

        # 1. Mark to market and check exits from open positions.
        prices_today = {s: float(h.loc[d, "Close"]) for s, h in histories.items() if d in h.index}
        book.mark_to_market(prices_today)

        for sym in list(book.positions.keys()):
            pos = book.positions[sym]
            gain = (pos.current_price - pos.avg_entry) / pos.avg_entry
            if gain >= profit_target_pct:
                pl = book.close_position(sym, pos.current_price)
                taken.append({
                    "date": d.strftime("%Y-%m-%d"), "symbol": sym,
                    "exit_reason": "target", "pnl": pl,
                })
            elif gain <= -stop_loss_pct:
                pl = book.close_position(sym, pos.current_price)
                taken.append({
                    "date": d.strftime("%Y-%m-%d"), "symbol": sym,
                    "exit_reason": "stop", "pnl": pl,
                })

        # 2. Regime gate (whole day; blocks all BUYs at once).
        snap = market_snapshot_for_date(macro, d)
        regime = regime_gate.classify(snap)
        regime_allow = regime.allow_new_buys

        # 3. For each symbol, look for a signal today.
        for sym in symbols:
            hist = histories.get(sym)
            if hist is None or hist.empty:
                continue
            sig = _rolling_signals(hist, sym, d)
            if sig is None:
                continue

            # ── regime stage ──
            if sig.action == "BUY" and not regime_allow:
                hits.append(GateHit(
                    d.strftime("%Y-%m-%d"), sym, sig.action,
                    "regime", "blocked_regime", regime.reason,
                ))
                _record_counterfactual(blocked_cf, hist, d, sym, sig.action,
                                       "regime", trade_dollars)
                continue

            # ── AI stage ──
            decision = ai_call(sig)
            if decision.action != sig.action or decision.confidence < ai_min_confidence:
                hits.append(GateHit(
                    d.strftime("%Y-%m-%d"), sym, sig.action,
                    "ai", "blocked_ai",
                    f"ai={decision.action}@{decision.confidence:.2f} {decision.reason[:80]}",
                ))
                _record_counterfactual(blocked_cf, hist, d, sym, sig.action,
                                       "ai", trade_dollars)
                continue

            # ── risk stage ──
            # Only BUYs use notional; SELLs pass 0.
            notional = trade_dollars if sig.action == "BUY" else 0.0
            with _patch_broker_state(book):
                risk = risk_gate.evaluate(sym, sig.action, notional)
            if not risk.approved:
                hits.append(GateHit(
                    d.strftime("%Y-%m-%d"), sym, sig.action,
                    "risk", "blocked_risk", risk.reason,
                ))
                _record_counterfactual(blocked_cf, hist, d, sym, sig.action,
                                       "risk", trade_dollars)
                continue

            # ── all gates approve — take the trade at next-day open ──
            nxt = _next_open(hist, d)
            if nxt is None:
                continue
            nd, open_px = nxt
            qty = max(1, int(trade_dollars // open_px))
            if sig.action == "BUY":
                book.open_position(sym, qty, open_px)
                hits.append(GateHit(
                    d.strftime("%Y-%m-%d"), sym, sig.action,
                    "taken", "allowed",
                    f"entered @ {open_px:.2f} on {nd.strftime('%Y-%m-%d')}",
                ))
            else:
                pl = book.close_position(sym, open_px)
                hits.append(GateHit(
                    d.strftime("%Y-%m-%d"), sym, sig.action,
                    "taken", "allowed",
                    f"exited @ {open_px:.2f} on {nd.strftime('%Y-%m-%d')} pnl={pl:.2f}",
                ))
                taken.append({
                    "date": nd.strftime("%Y-%m-%d"), "symbol": sym,
                    "exit_reason": "signal", "pnl": pl,
                })

        # 4. Equity curve tick.
        equity_curve.append({
            "date": d.strftime("%Y-%m-%d"),
            "equity": book.account_pnl()["equity"],
            "cash": book.cash,
            "open_positions": len(book.positions),
        })

    return RunResult(hits=hits, blocked_counterfactual=blocked_cf,
                     taken_pnl=taken, equity_curve=equity_curve)


def _record_counterfactual(
    blocked: list, hist: pd.DataFrame, d: pd.Timestamp,
    sym: str, action: str, stage: str, trade_dollars: float,
) -> None:
    """For a blocked signal, look 5 trading days forward and record the
    hypothetical stock-direction P&L. That's the "did the gate save money
    or leave money on the table" measurement."""
    after = hist.loc[hist.index > d].head(5)
    if after.empty:
        return
    entry = float(hist.loc[d, "Close"])
    exit_ = float(after["Close"].iloc[-1])
    pct = (exit_ - entry) / entry
    if action == "SELL":
        pct = -pct   # SELL wins on downside
    hyp_pnl = pct * trade_dollars
    blocked.append({
        "date": d.strftime("%Y-%m-%d"), "symbol": sym, "action": action,
        "blocked_by": stage,
        "entry_close": entry,
        "exit_close_5d": exit_,
        "underlying_pct_5d": pct,
        "hypothetical_pnl_5d": hyp_pnl,
    })


def _download_ohlcv(sym: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


# ── Broker patcher — installs the synthetic book behind risk_gate ────────────

class _patch_broker_state:
    def __init__(self, book: SyntheticBook) -> None:
        self._book = book
        self._patches: list = []

    def __enter__(self):
        # RiskGate calls alpaca_client.get_account_pnl() and get_positions_pnl()
        # by module-attribute lookup on the alpaca_client instance imported at
        # module load time. Patch the bound methods on that instance.
        p1 = patch(
            "app.services.risk_gate.alpaca_client.get_account_pnl",
            new=lambda: self._book.account_pnl(),
        )
        p2 = patch(
            "app.services.risk_gate.alpaca_client.get_positions_pnl",
            new=lambda: self._book.positions_pnl(),
        )
        p1.start(); p2.start()
        self._patches = [p1, p2]
        return self

    def __exit__(self, exc_type, exc, tb):
        for p in self._patches:
            p.stop()
        self._patches = []
        return False


# ── Output writers ───────────────────────────────────────────────────────────

DISCLOSURE = (
    "This backtest is a hypothetical historical simulation. It does not "
    "represent actual trading performance. P&L is computed on the STOCK "
    "underlying, not the option, because the intent is to measure gate "
    "quality (regime / AI / risk), not option-strategy P&L. Real option "
    "trades would differ due to IV, decay, slippage, and liquidity. This "
    "material is for research and educational purposes only. Review "
    "Alpaca's disclosures at alpaca.markets/disclosures."
)


def write_report(out_dir: Path, result: RunResult, meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    def _dump(rows: list, name: str, fields: list) -> None:
        with (out_dir / name).open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow(r if isinstance(r, dict) else r.__dict__)

    _dump(result.hits, "gate_hits.csv",
          ["date", "symbol", "action", "stage", "outcome", "reason"])
    _dump(result.blocked_counterfactual, "blocked_pnl.csv",
          ["date", "symbol", "action", "blocked_by", "entry_close",
           "exit_close_5d", "underlying_pct_5d", "hypothetical_pnl_5d"])
    _dump(result.taken_pnl, "taken_pnl.csv",
          ["date", "symbol", "exit_reason", "pnl"])
    _dump(result.equity_curve, "equity_curve.csv",
          ["date", "equity", "cash", "open_positions"])

    summary = {
        "meta": meta,
        "disclosure": DISCLOSURE,
        "gate_hit_counts": _tally(result.hits),
        "blocked_pnl_by_stage": _blocked_pnl_by_stage(result.blocked_counterfactual),
        "taken_pnl_total": round(sum(t["pnl"] for t in result.taken_pnl), 2),
        "final_equity": (
            result.equity_curve[-1]["equity"] if result.equity_curve else None
        ),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))


def _tally(hits: List[GateHit]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for h in hits:
        out[h.outcome] = out.get(h.outcome, 0) + 1
    return out


def _blocked_pnl_by_stage(rows: List[dict]) -> Dict[str, dict]:
    """For each stage, sum hypothetical P&L. Positive = gate cost us money;
    negative = gate saved us money."""
    out: Dict[str, dict] = {}
    for r in rows:
        stage = r["blocked_by"]
        cell = out.setdefault(stage, {"count": 0, "hypothetical_pnl": 0.0})
        cell["count"] += 1
        cell["hypothetical_pnl"] += float(r["hypothetical_pnl_5d"])
    for stage, cell in out.items():
        cell["hypothetical_pnl"] = round(cell["hypothetical_pnl"], 2)
    return out


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Full-stack Regime→AI→Risk backtest")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end",   required=True, help="YYYY-MM-DD")
    ap.add_argument("--symbols", default="AAPL,SPY,QQQ",
                    help="Comma-separated tickers")
    ap.add_argument("--out-dir", default="runs",
                    help="Parent runs directory (a timestamp subfolder is created)")
    ap.add_argument("--live-ai", action="store_true",
                    help="Call the real ai_advisor (uses cache). Off by default.")
    ap.add_argument("--stub-ai", dest="live_ai", action="store_false",
                    help="Use a deterministic pass-through stub advisor (default).")
    ap.set_defaults(live_ai=False)
    ap.add_argument("--cache-path", default="runs/ai_decision_cache.sqlite")
    ap.add_argument("--trade-dollars", type=float, default=1000.0)
    ap.add_argument("--ai-min-confidence", type=float, default=0.65)
    ap.add_argument("--ai-call-delay-ms", type=int, default=0,
                    help="Sleep this long between live LLM calls to stay "
                         "under provider rate limits (and keep the advisor's "
                         "circuit breaker closed). 250-500ms works for Groq.")
    ap.add_argument("--purge-fail-closed", action="store_true",
                    help="Delete any cached decisions whose reason indicates "
                         "the advisor short-circuited (circuit-open, kill "
                         "switch). Run this once if a prior backtest tripped "
                         "the breaker and poisoned the cache.")
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) / f"full_stack_{ts}"

    cache = AIDecisionCache(args.cache_path)
    if args.purge_fail_closed:
        by_outcome = cache.purge_fail_closed()
        by_reason = cache.purge_by_reason(list(_LEGACY_FAIL_CLOSED_REASON_MARKERS))
        print(f"[backtest] purged {by_outcome} fail-closed cache entries "
              f"by outcome + {by_reason} legacy entries by reason match")

    if args.live_ai:
        reset_advisor_circuit()

    ai_call = make_ai_callable(
        live=args.live_ai, cache=cache,
        per_call_delay_s=args.ai_call_delay_ms / 1000.0,
    )
    book = SyntheticBook()

    result = run_backtest(
        start=args.start, end=args.end, symbols=symbols,
        ai_call=ai_call, book=book,
        trade_dollars=args.trade_dollars,
        ai_min_confidence=args.ai_min_confidence,
    )

    write_report(out_dir, result, meta={
        "start": args.start, "end": args.end, "symbols": symbols,
        "live_ai": args.live_ai, "cache_stats": cache.stats(),
        "trade_dollars": args.trade_dollars,
        "ai_min_confidence": args.ai_min_confidence,
    })
    print(f"[backtest] wrote {out_dir}")
    print(f"[backtest] cache: {cache.stats()}")


if __name__ == "__main__":
    main()
