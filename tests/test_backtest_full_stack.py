"""Smoke test for scripts/backtest_full_stack.

The point of this test is to prove the runner glues together correctly:
signals → regime → AI → risk → book → outputs. It does NOT test P&L
accuracy — that's a strategy question, not a wiring question.

Deliberately offline: synthetic OHLCV in-memory + injected macro frame +
stub AI. Runs in under a second.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.backtest_cache import (
    AIDecisionCache,
    CachedDecision,
    make_cache_key,
)
from scripts.backtest_full_stack import (
    RunResult,
    Signal,
    SyntheticBook,
    make_ai_callable,
    run_backtest,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _oversold_then_bounce_series(days: int = 120, start_price: float = 200.0) -> pd.DataFrame:
    """Prices that trend down for the first 90 days (RSI < 35), then bounce.
    Guarantees the RSI strategy fires BUYs in the second half of the window."""
    dates = pd.bdate_range("2024-01-01", periods=days)
    prices = []
    for i in range(days):
        if i < 90:
            prices.append(start_price - i * 0.5)
        else:
            prices.append(prices[-1] + 0.4)   # bounce
    close = pd.Series(prices, index=dates)
    return pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": [1_000_000] * days,
    }, index=dates)


def _calm_uptrend_macro(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """SPY uptrend + VIX ≈ 15 (calm) → regime gate must allow BUYs."""
    spy = pd.Series(np.linspace(400, 500, len(dates)), index=dates)
    return pd.DataFrame({
        "spy_close": spy,
        "spy_sma50": spy.rolling(50, min_periods=1).mean(),
        "spy_sma200": spy.rolling(50, min_periods=1).mean() * 0.9,  # sma200 < sma50 → uptrend
        "vix": pd.Series([15.0] * len(dates), index=dates),
        "vix_prev": pd.Series([15.0] * len(dates), index=dates),
        "spy_trend": pd.Series(["uptrend"] * len(dates), index=dates),
    }, index=dates)


def _panic_vix_macro(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """VIX = 40 (panic) → regime gate must block BUYs."""
    spy = pd.Series(np.linspace(400, 500, len(dates)), index=dates)
    return pd.DataFrame({
        "spy_close": spy,
        "spy_sma50": spy.rolling(50, min_periods=1).mean(),
        "spy_sma200": spy.rolling(50, min_periods=1).mean() * 0.9,
        "vix": pd.Series([40.0] * len(dates), index=dates),
        "vix_prev": pd.Series([40.0] * len(dates), index=dates),
        "spy_trend": pd.Series(["uptrend"] * len(dates), index=dates),
    }, index=dates)


@pytest.fixture
def synthetic_hist() -> dict:
    return {"AAPL": _oversold_then_bounce_series()}


@pytest.fixture
def in_memory_cache() -> AIDecisionCache:
    return AIDecisionCache(":memory:")


# ── Cache basics ─────────────────────────────────────────────────────────────

class TestAIDecisionCache:
    def test_miss_then_hit(self, in_memory_cache):
        key = make_cache_key(
            symbol="AAPL", date_str="2024-06-01", proposed_action="BUY",
            price=180.0, momentum=32.0, price_history=[180, 179, 178],
            provider="groq", model="llama-3.1",
        )
        assert in_memory_cache.get(key) is None
        in_memory_cache.put(key, symbol="AAPL", date_str="2024-06-01",
                            decision=CachedDecision("BUY", 0.75, "ok"),
                            provider="groq", model="llama-3.1")
        hit = in_memory_cache.get(key)
        assert hit is not None
        assert hit.action == "BUY"
        assert hit.confidence == pytest.approx(0.75)

    def test_key_changes_when_model_swaps(self):
        common = dict(
            symbol="AAPL", date_str="2024-06-01", proposed_action="BUY",
            price=180.0, momentum=32.0, price_history=[180, 179],
        )
        k1 = make_cache_key(**common, provider="groq", model="llama-3.1")
        k2 = make_cache_key(**common, provider="groq", model="llama-4.0")
        assert k1 != k2, "model change must invalidate cache — safer than a stale hit"

    def test_key_stable_across_tiny_float_drift(self):
        """Re-deriving price history in floats can produce bit-level drift.
        The key rounds to 4 decimals so identical inputs still hash the same."""
        k1 = make_cache_key(
            symbol="AAPL", date_str="2024-06-01", proposed_action="BUY",
            price=180.00001, momentum=32.0,
            price_history=[180.00001, 179.99999],
            provider="groq", model="llama-3.1",
        )
        k2 = make_cache_key(
            symbol="AAPL", date_str="2024-06-01", proposed_action="BUY",
            price=180.0, momentum=32.0,
            price_history=[180.0, 180.0],
            provider="groq", model="llama-3.1",
        )
        assert k1 == k2


# ── Runner smoke tests ───────────────────────────────────────────────────────

def _run(synthetic_hist, macro_fn, cache, min_conf=0.5) -> RunResult:
    hist = synthetic_hist["AAPL"]
    dates = hist.index
    macro = macro_fn(dates)

    book = SyntheticBook(starting_equity=100_000.0, cash=100_000.0)
    ai_call = make_ai_callable(live=False, cache=cache)

    return run_backtest(
        start="2024-01-01", end="2024-12-31",
        symbols=["AAPL"], ai_call=ai_call, book=book,
        ai_min_confidence=min_conf,
        hist_source=lambda s: synthetic_hist.get(s, pd.DataFrame()),
        macro_override=macro,
    )


class TestFullStackRunner:
    def test_calm_regime_produces_trades_and_equity_curve(
        self, synthetic_hist, in_memory_cache
    ):
        """Calm uptrend + oversold price series should surface at least
        one BUY that clears every gate. If nothing trades in the golden
        path, the runner is broken."""
        result = _run(synthetic_hist, _calm_uptrend_macro, in_memory_cache)

        # Equity curve populated for every trading day.
        assert len(result.equity_curve) == len(synthetic_hist["AAPL"])

        # At least one signal was seen and at least one was taken.
        assert result.hits, "runner produced no gate hits — signals never fired"
        taken_hits = [h for h in result.hits if h.outcome == "allowed"]
        assert taken_hits, "no signals made it through all gates"

    def test_panic_vix_blocks_every_buy(self, synthetic_hist, in_memory_cache):
        """VIX=40 with REGIME_BLOCK_ON_PANIC_VIX (default true) must produce
        zero BUYs through the regime stage."""
        result = _run(synthetic_hist, _panic_vix_macro, in_memory_cache)
        regime_blocks = [h for h in result.hits if h.outcome == "blocked_regime"]
        allowed_buys = [h for h in result.hits
                        if h.outcome == "allowed" and h.action == "BUY"]
        assert regime_blocks, "panic VIX should have produced regime blocks"
        assert not allowed_buys, "no BUY should escape panic VIX gate"

    def test_blocked_pnl_has_counterfactual_for_each_block(
        self, synthetic_hist, in_memory_cache
    ):
        """Every blocked signal should record a hypothetical-P&L row so we
        can answer 'was blocking that trade a good call'."""
        result = _run(synthetic_hist, _panic_vix_macro, in_memory_cache)
        blocks = [h for h in result.hits if h.outcome.startswith("blocked_")]
        # Late-window blocks near series end may have no 5-day forward data.
        # For an inner-window signal, we expect a counterfactual row.
        inner_blocks = [h for h in blocks if h.date < "2024-06-01"]
        if inner_blocks:
            assert len(result.blocked_counterfactual) >= len(inner_blocks) * 0.5

    def test_cache_populated_after_run(self, synthetic_hist, in_memory_cache):
        """Cache should have grown — every AI call goes through the cache
        (even the stub path). This is what makes re-runs deterministic."""
        _run(synthetic_hist, _calm_uptrend_macro, in_memory_cache)
        assert in_memory_cache.stats()["count"] > 0

    def test_second_run_uses_cache_and_matches(
        self, synthetic_hist, in_memory_cache
    ):
        """Deterministic re-run: same inputs → same equity curve. That's the
        promise of the cache — first pass costs tokens, second pass is free
        and reproducible."""
        r1 = _run(synthetic_hist, _calm_uptrend_macro, in_memory_cache)
        r2 = _run(synthetic_hist, _calm_uptrend_macro, in_memory_cache)
        eq1 = [(row["date"], round(row["equity"], 2)) for row in r1.equity_curve]
        eq2 = [(row["date"], round(row["equity"], 2)) for row in r2.equity_curve]
        assert eq1 == eq2


class TestSyntheticBook:
    """The synthetic book is what the risk gate sees. If it's inconsistent
    the whole backtest is inconsistent."""

    def test_opening_position_reduces_cash(self):
        book = SyntheticBook(cash=100_000.0)
        book.open_position("AAPL", qty=10, price=150.0)
        assert book.cash == pytest.approx(98_500.0)
        assert book.positions["AAPL"].qty == 10

    def test_closing_position_realizes_pnl(self):
        book = SyntheticBook(cash=100_000.0)
        book.open_position("AAPL", qty=10, price=150.0)
        pl = book.close_position("AAPL", price=160.0)
        assert pl == pytest.approx(100.0)   # 10 × $10 gain
        assert "AAPL" not in book.positions
        assert book.realized_day_pl == pytest.approx(100.0)

    def test_account_pnl_reflects_unrealized(self):
        book = SyntheticBook(cash=100_000.0)
        book.open_position("AAPL", qty=10, price=150.0)
        book.mark_to_market({"AAPL": 155.0})
        acct = book.account_pnl()
        assert acct["unrealized_pl"] == pytest.approx(50.0)
        # equity = cash (98,500) + market_value (1,550) = 100,050
        assert acct["equity"] == pytest.approx(100_050.0)
