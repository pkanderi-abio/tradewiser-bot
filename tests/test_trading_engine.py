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
    _parse_underlying,
    momentum_strategy,
    rsi_strategy,
)


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
