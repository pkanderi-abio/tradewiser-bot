"""Tests for DailyRSIStrategy in app/services/trading_engine.py"""
import pandas as pd
import pytest
from unittest.mock import patch

from app.services.trading_engine import (
    DailyRSIStrategy,
    MAX_POSITIONS,
    RSI_BUY_THRESHOLD,
    RSI_SELL_THRESHOLD,
    TRADE_QUANTITY,
    _compute_rsi,
    _days_to_expiry,
    _estimate_option_notional,
    _parse_underlying,
    _resolve_option_and_notional,
    get_daily_signal,
    momentum_strategy,
    rsi_strategy,
)
from app.core.config import settings


# ---------------------------------------------------------------------------
# RSI calculation
# ---------------------------------------------------------------------------

class TestComputeRsi:
    def test_returns_50_when_insufficient_data(self):
        # Less than period+1 bars → fallback midpoint
        assert _compute_rsi(pd.Series([100.0, 101.0]), period=14) == 50.0

    def test_uptrend_with_minor_pullbacks_pushes_rsi_high(self):
        # Mostly-up series with real down bars (drop of 1.0 every 5th step
        # against +1.0 gains) → RSI should be high. Pure no-loss series produces
        # NaN due to /0 in the avg_loss term; real markets always have some down ticks.
        base = [100.0]
        for i in range(1, 30):
            base.append(base[-1] - 0.5 if i % 5 == 0 else base[-1] + 1.0)
        rsi = _compute_rsi(pd.Series(base), period=14)
        assert rsi > 70

    def test_downtrend_with_minor_bounces_pushes_rsi_low(self):
        base = [200.0]
        for i in range(1, 30):
            base.append(base[-1] + 0.5 if i % 5 == 0 else base[-1] - 1.0)
        rsi = _compute_rsi(pd.Series(base), period=14)
        assert rsi < 30


# ---------------------------------------------------------------------------
# OCC symbol parsing helpers
# ---------------------------------------------------------------------------

class TestOccParsing:
    def test_parse_underlying_extracts_ticker(self):
        assert _parse_underlying("O:AAPL250117C00185000") == "AAPL"
        assert _parse_underlying("AAPL250117C00185000") == "AAPL"

    def test_parse_underlying_returns_none_for_garbage(self):
        assert _parse_underlying("not-an-occ-symbol") is None

    def test_days_to_expiry_returns_int_for_valid_symbol(self):
        # YYMMDD = 991231 is in the past; expect negative int
        result = _days_to_expiry("O:AAPL991231C00100000")
        assert isinstance(result, int)
        assert result < 0

    def test_days_to_expiry_returns_none_for_garbage(self):
        assert _days_to_expiry("not-an-occ") is None


# ---------------------------------------------------------------------------
# DailyRSIStrategy state management
# ---------------------------------------------------------------------------

class TestStrategyState:
    def test_starts_with_no_positions(self):
        s = DailyRSIStrategy()
        assert s.active_position_count() == 0
        assert s.has_capacity() is True
        assert s.has_position("AAPL") is False

    def test_has_capacity_returns_false_when_max_reached(self):
        s = DailyRSIStrategy()
        for i in range(MAX_POSITIONS):
            s.positions[f"SYM{i}"] = 1
        assert s.has_capacity() is False
        assert s.active_position_count() == MAX_POSITIONS

    def test_get_status_returns_serializable_dict(self):
        s = DailyRSIStrategy()
        s.positions["AAPL"] = 1
        s.option_symbols["AAPL"] = "O:AAPL250117C00185000"
        s.entry_opt_prices["AAPL"] = 2.50

        status = s.get_status()
        assert status["positions"] == {"AAPL": 1}
        assert status["option_symbols"]["AAPL"] == "O:AAPL250117C00185000"
        assert status["entry_opt_prices"]["AAPL"] == 2.50

    def test_momentum_strategy_is_alias_for_rsi_strategy(self):
        assert momentum_strategy is rsi_strategy


# ---------------------------------------------------------------------------
# Buy / sell execution (with alpaca mocked)
# ---------------------------------------------------------------------------

class TestExecuteBuy:
    def test_skips_when_no_call_symbol(self):
        s = DailyRSIStrategy()
        with patch("app.services.trading_engine.alpaca_client") as mock_alpaca:
            assert s.execute_buy("AAPL", 150.0, "") is False
            mock_alpaca.place_order.assert_not_called()
        assert s.has_position("AAPL") is False

    def test_records_position_on_alpaca_success(self):
        s = DailyRSIStrategy()
        with patch("app.services.trading_engine.alpaca_client") as mock_alpaca:
            mock_alpaca.place_order.return_value = {"limit_price": 2.50, "id": "ord-1"}
            ok = s.execute_buy("AAPL", 150.0, "O:AAPL250117C00150000")

        assert ok is True
        assert s.has_position("AAPL") is True
        assert s.option_symbols["AAPL"] == "O:AAPL250117C00150000"
        assert s.entry_opt_prices["AAPL"] == 2.50
        assert s.entry_stock_prices["AAPL"] == 150.0
        assert s.peak_opt_prices["AAPL"] == 2.50

    def test_no_position_when_alpaca_rejects(self):
        s = DailyRSIStrategy()
        with patch("app.services.trading_engine.alpaca_client") as mock_alpaca:
            mock_alpaca.place_order.return_value = None
            ok = s.execute_buy("AAPL", 150.0, "O:AAPL250117C00150000")
        assert ok is False
        assert s.has_position("AAPL") is False


class TestExecuteSell:
    def test_returns_false_when_no_option_held(self):
        s = DailyRSIStrategy()
        with patch("app.services.trading_engine.alpaca_client") as mock_alpaca:
            assert s.execute_sell("AAPL", 150.0) is False
            mock_alpaca.place_order.assert_not_called()

    def test_clears_state_on_full_exit(self):
        s = DailyRSIStrategy()
        s.positions["AAPL"] = 1
        s.option_symbols["AAPL"] = "O:AAPL250117C00150000"
        s.entry_opt_prices["AAPL"] = 2.50
        s.entry_stock_prices["AAPL"] = 150.0
        s.peak_opt_prices["AAPL"] = 3.00

        with patch("app.services.trading_engine.alpaca_client") as mock_alpaca:
            mock_alpaca.place_order.return_value = {"id": "ord-2"}
            ok = s.execute_sell("AAPL", 155.0, reason="profit target")

        assert ok is True
        assert s.has_position("AAPL") is False
        assert "AAPL" not in s.option_symbols
        assert "AAPL" not in s.entry_opt_prices


# ---------------------------------------------------------------------------
# Constants sanity (these are critical safety values)
# ---------------------------------------------------------------------------

class TestStrategyConstants:
    def test_rsi_thresholds_are_in_valid_range(self):
        assert 0 < RSI_BUY_THRESHOLD < RSI_SELL_THRESHOLD < 100

    def test_max_positions_is_positive(self):
        assert MAX_POSITIONS > 0

    def test_trade_quantity_is_positive(self):
        assert TRADE_QUANTITY > 0


# ---------------------------------------------------------------------------
# STRATEGY_REQUIRE_UPTREND_FILTER — toggles the price>SMA50 trend gate
# ---------------------------------------------------------------------------

def _build_history(close_series, vol_series=None):
    """yfinance-shaped DataFrame the get_daily_signal code path expects."""
    if vol_series is None:
        vol_series = [1_000_000] * len(close_series)
    idx = pd.date_range(end=pd.Timestamp.utcnow(), periods=len(close_series), freq="D")
    return pd.DataFrame(
        {"Close": close_series, "Volume": vol_series, "High": close_series,
         "Low": close_series, "Open": close_series},
        index=idx,
    )


class TestUptrendFilter:
    """The user can disable the trend filter to allow buying oversold names in
    downtrends. Default is True (preserve historic behavior); when False, the
    RSI<35 check alone is enough to produce a BUY signal."""

    def _oversold_downtrend_history(self):
        # Build a series that ends well below its 50-day SMA with a low RSI.
        # 200 days of falling prices: starts at 200, ends near 100 → RSI low,
        # price way under SMA50.
        prices = [200 - i * 0.5 for i in range(200)]
        return _build_history(prices)

    def _patch_signal_inputs(self, monkeypatch, hist):
        """Patch the data sources get_daily_signal uses so the test is offline
        and so we isolate the trend-filter behavior from the HV-rank and
        earnings gates (each tested separately in their own scope)."""
        monkeypatch.setattr("app.services.trading_engine._get_days_to_earnings",
                            lambda s: None)
        monkeypatch.setattr("app.services.trading_engine._get_hv_rank_from_hist",
                            lambda h: 30.0)  # below IV_RANK_MAX=50
        ticker_mock = patch("app.services.trading_engine.yf.Ticker").start()
        ticker_mock.return_value.history.return_value = hist
        return ticker_mock

    def test_default_filter_blocks_buy_in_downtrend(self, monkeypatch):
        """Default (filter on) — oversold-but-below-SMA50 must NOT BUY."""
        monkeypatch.setattr(settings, "STRATEGY_REQUIRE_UPTREND_FILTER", True)
        self._patch_signal_inputs(monkeypatch, self._oversold_downtrend_history())
        try:
            sig = get_daily_signal("FAKE")
        finally:
            patch.stopall()
        assert sig["signal"] != "BUY"
        assert sig["rsi"] is not None
        assert sig["rsi"] < RSI_BUY_THRESHOLD  # confirm it was actually oversold

    def test_relaxed_filter_allows_buy_in_downtrend(self, monkeypatch):
        """Filter off — same oversold downtrend setup should now produce BUY."""
        monkeypatch.setattr(settings, "STRATEGY_REQUIRE_UPTREND_FILTER", False)
        self._patch_signal_inputs(monkeypatch, self._oversold_downtrend_history())
        try:
            sig = get_daily_signal("FAKE")
        finally:
            patch.stopall()
        assert sig["signal"] == "BUY", f"expected BUY but got {sig}"
        assert sig["rsi"] < RSI_BUY_THRESHOLD


# ---------------------------------------------------------------------------
# _resolve_option_and_notional — feeds real premium into the risk gate
# ---------------------------------------------------------------------------

class TestResolveOptionAndNotional:
    """The risk gate cares whether one position exceeds a % of equity. The old
    5% heuristic under-fired on high-IV names (e.g. TSLA weeklies routinely
    price at 8-12% of underlying), so concentration caps let through positions
    they shouldn't have. This helper now feeds the risk gate the real ask."""

    def test_uses_live_ask_when_quote_available(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.watchlist_manager.get_atm_option_symbols",
            lambda s, w: ["O:AAPL260117C00185000"],
        )
        monkeypatch.setattr(
            "app.services.trading_engine.alpaca_client.get_batch_quotes",
            lambda syms: {"O:AAPL260117C00185000": {"ask": 4.20, "bid": 4.10}},
        )
        opt_sym, notional = _resolve_option_and_notional("AAPL", 185.0, 1, 4)
        assert opt_sym == "O:AAPL260117C00185000"
        # ask=4.20 * 100 shares * 1 contract = $420 — the real cost, not 5%×185×100=$925
        assert notional == pytest.approx(420.0)

    def test_falls_back_to_heuristic_when_no_ask(self, monkeypatch):
        """Weekend / pre-market: quote comes back empty. Fall back to 5%
        heuristic — the risk gate must still see a positive number so
        concentration checks don't silently disable themselves."""
        monkeypatch.setattr(
            "app.services.watchlist_manager.get_atm_option_symbols",
            lambda s, w: ["O:AAPL260117C00185000"],
        )
        monkeypatch.setattr(
            "app.services.trading_engine.alpaca_client.get_batch_quotes",
            lambda syms: {},  # feed empty (pre-market)
        )
        opt_sym, notional = _resolve_option_and_notional("AAPL", 185.0, 1, 4)
        assert opt_sym == "O:AAPL260117C00185000"
        assert notional == pytest.approx(_estimate_option_notional(185.0, 1))
        assert notional > 0

    def test_falls_back_when_quote_call_raises(self, monkeypatch):
        """Broker outage during quote lookup. ATM symbol resolved fine, so
        we still know what we'd trade — just size it via the heuristic."""
        monkeypatch.setattr(
            "app.services.watchlist_manager.get_atm_option_symbols",
            lambda s, w: ["O:AAPL260117C00185000"],
        )
        def _boom(_syms):
            raise RuntimeError("alpaca down")
        monkeypatch.setattr(
            "app.services.trading_engine.alpaca_client.get_batch_quotes", _boom
        )
        opt_sym, notional = _resolve_option_and_notional("AAPL", 185.0, 1, 4)
        assert opt_sym == "O:AAPL260117C00185000"
        assert notional == pytest.approx(_estimate_option_notional(185.0, 1))

    def test_returns_none_when_atm_lookup_empty(self, monkeypatch):
        """If we can't even resolve an ATM symbol, the caller should skip the
        trade entirely — no symbol, no order."""
        monkeypatch.setattr(
            "app.services.watchlist_manager.get_atm_option_symbols",
            lambda s, w: [],
        )
        opt_sym, notional = _resolve_option_and_notional("AAPL", 185.0, 1, 4)
        assert opt_sym is None
        assert notional == 0.0

    def test_returns_none_when_atm_lookup_raises(self, monkeypatch):
        """Same treatment on exception — never fabricate a trade."""
        def _boom(_s, _w):
            raise RuntimeError("yfinance rate limit")
        monkeypatch.setattr(
            "app.services.watchlist_manager.get_atm_option_symbols", _boom
        )
        opt_sym, notional = _resolve_option_and_notional("AAPL", 185.0, 1, 4)
        assert opt_sym is None
        assert notional == 0.0

    def test_scales_by_quantity(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.watchlist_manager.get_atm_option_symbols",
            lambda s, w: ["O:SPY260117C00500000"],
        )
        monkeypatch.setattr(
            "app.services.trading_engine.alpaca_client.get_batch_quotes",
            lambda syms: {"O:SPY260117C00500000": {"ask": 3.00}},
        )
        _, notional_1 = _resolve_option_and_notional("SPY", 500.0, 1, 4)
        _, notional_5 = _resolve_option_and_notional("SPY", 500.0, 5, 4)
        assert notional_1 == pytest.approx(300.0)
        assert notional_5 == pytest.approx(1500.0)
