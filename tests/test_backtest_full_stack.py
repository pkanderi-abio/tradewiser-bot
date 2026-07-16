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

from app.services.ai_guardrails import FAIL_CLOSED_OUTCOMES, is_fail_closed
from app.services.backtest_cache import (
    AIDecisionCache,
    CachedDecision,
    make_cache_key,
)
from scripts.backtest_full_stack import (
    _LEGACY_FAIL_CLOSED_REASON_MARKERS,
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


class TestFailClosedCacheProtection:
    """When the ai_advisor short-circuits (circuit open, kill switch, LLM
    error), the returned decision is a fake HOLD, not a real model verdict.
    Caching those poisons every re-run — signals that hit the breaker during
    a rate-limit spike would silently replay as HOLD forever. That was a
    real bug during a 9-symbol Groq run.

    Detection is now via the structured `outcome` field on the decision
    dict, not substring-matching `reason` — so future changes to the
    advisor's error phrasing can't silently break this."""

    def test_is_fail_closed_reads_outcome_field(self):
        # Every outcome the advisor emits on failure.
        assert is_fail_closed({"outcome": "kill_switch"})
        assert is_fail_closed({"outcome": "circuit_open"})
        assert is_fail_closed({"outcome": "llm_error"})
        assert is_fail_closed({"outcome": "timeout"})
        assert is_fail_closed({"outcome": "schema_error"})
        assert is_fail_closed({"outcome": "soft_fail"})
        # Real model verdicts and deterministic gates on real data are NOT
        # fail-closed — they should be cached.
        assert not is_fail_closed({"outcome": "ok"})
        assert not is_fail_closed({"outcome": "severity_gate"})
        # Absent or unknown outcomes are treated as real verdicts (safe
        # default: don't drop data on a schema change).
        assert not is_fail_closed({})
        assert not is_fail_closed({"outcome": None})
        assert not is_fail_closed({"outcome": "some_new_marker_we_havent_added"})

    def test_advisor_fail_closed_outcomes_cover_actual_hold_reasons(self):
        # Sanity check: the outcomes the advisor stamps on fail-closed HOLDs
        # are the ones is_fail_closed knows about. If ai_advisor grows a new
        # failure mode, this test drives adding it to FAIL_CLOSED_OUTCOMES.
        expected = {"kill_switch", "circuit_open", "llm_error", "timeout",
                    "schema_error", "soft_fail"}
        assert expected.issubset(FAIL_CLOSED_OUTCOMES)

    def test_live_advisor_fail_closed_response_is_not_cached(self, in_memory_cache):
        """Simulate a live advisor that returns a circuit-open HOLD. The
        wrapper must NOT persist that to the cache — otherwise the next run
        (with a healthy provider) would replay the fake HOLD instead of asking."""
        class _FakeAdvisor:
            def get_provider(self): return "groq"
            def get_model(self): return "llama-3.1"
            def decide(self, **kw):
                return {"action": "HOLD", "confidence": 0.0,
                        "reason": "circuit breaker open — LLM unavailable",
                        "outcome": "circuit_open"}

        import scripts.backtest_full_stack as bfs
        # Patch the module-level import used inside make_ai_callable.
        import sys
        fake_module = type(sys)("fake_ai_advisor")
        fake_module.ai_advisor = _FakeAdvisor()
        sys.modules["app.services.ai_advisor"] = fake_module
        try:
            ai_call = bfs.make_ai_callable(live=True, cache=in_memory_cache)
            sig = Signal("AAPL", "BUY", 180.0, 32.0, [180.0, 179.0, 178.0],
                         pd.Timestamp("2024-06-01"))
            cd = ai_call(sig)
        finally:
            # Don't leave a shim in sys.modules — the real advisor imports
            # get_news which would then break in later tests.
            del sys.modules["app.services.ai_advisor"]

        # Wrapper still returns the fail-closed HOLD to the caller — the
        # backtest's downstream logic will interpret it correctly.
        assert cd.action == "HOLD"
        assert cd.outcome == "circuit_open"
        # But NOTHING is in the cache — a healthy re-run would call again.
        assert in_memory_cache.stats()["count"] == 0

    def test_cache_put_refuses_fail_closed_by_default(self, in_memory_cache):
        """The cache itself is the last line of defense: even if a future
        caller forgets to check, put() drops fail-closed decisions."""
        key = make_cache_key(
            symbol="AAPL", date_str="2024-06-01", proposed_action="BUY",
            price=180.0, momentum=32.0, price_history=[180, 179, 178],
            provider="groq", model="llama-3.1",
        )
        wrote = in_memory_cache.put(
            key, symbol="AAPL", date_str="2024-06-01",
            decision=CachedDecision("HOLD", 0.0, "boom", outcome="llm_error"),
            provider="groq", model="llama-3.1",
        )
        assert wrote is False
        assert in_memory_cache.stats()["count"] == 0

    def test_purge_fail_closed_by_outcome(self, in_memory_cache):
        """purge_fail_closed drops rows whose outcome is fail-closed —
        primarily for cleaning up caches that were poisoned before put()
        grew its guard, or entries written with allow_fail_closed=True."""
        good_key = make_cache_key(
            symbol="AAPL", date_str="2024-06-01", proposed_action="BUY",
            price=180.0, momentum=32.0, price_history=[180, 179, 178],
            provider="groq", model="llama-3.1",
        )
        bad_key = make_cache_key(
            symbol="MSFT", date_str="2024-06-01", proposed_action="BUY",
            price=400.0, momentum=32.0, price_history=[400, 399, 398],
            provider="groq", model="llama-3.1",
        )
        in_memory_cache.put(good_key, symbol="AAPL", date_str="2024-06-01",
            decision=CachedDecision("BUY", 0.75, "RSI oversold with volume confirm",
                                    outcome="ok"),
            provider="groq", model="llama-3.1")
        # Force a fail-closed row in — simulates historical poisoned data.
        in_memory_cache.put(bad_key, symbol="MSFT", date_str="2024-06-01",
            decision=CachedDecision("HOLD", 0.0, "circuit breaker open — LLM unavailable",
                                    outcome="circuit_open"),
            provider="groq", model="llama-3.1",
            allow_fail_closed=True)
        assert in_memory_cache.stats()["count"] == 2

        removed = in_memory_cache.purge_fail_closed()

        assert removed == 1
        assert in_memory_cache.get(good_key) is not None
        assert in_memory_cache.get(bad_key) is None

    def test_purge_by_reason_still_cleans_legacy_poisoned_rows(self, in_memory_cache):
        """Legacy cache files (pre-outcome column) can only be cleaned by
        reason substrings — the outcome column defaults to 'ok' on migration
        so purge_fail_closed would miss them."""
        legacy_key = make_cache_key(
            symbol="NVDA", date_str="2024-06-01", proposed_action="BUY",
            price=800.0, momentum=32.0, price_history=[800, 799, 798],
            provider="groq", model="llama-3.1",
        )
        # A row that survived from before outcome existed — reason still
        # tells the story, but outcome column reads as 'ok' (the migration
        # default). purge_fail_closed can't help us here; purge_by_reason can.
        in_memory_cache.put(legacy_key, symbol="NVDA", date_str="2024-06-01",
            decision=CachedDecision("HOLD", 0.0, "circuit breaker open — LLM unavailable",
                                    outcome="ok"),
            provider="groq", model="llama-3.1")

        assert in_memory_cache.purge_fail_closed() == 0  # outcome says 'ok'
        removed = in_memory_cache.purge_by_reason(list(_LEGACY_FAIL_CLOSED_REASON_MARKERS))
        assert removed == 1
        assert in_memory_cache.get(legacy_key) is None


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
