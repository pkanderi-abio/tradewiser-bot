"""Tests for the MomentumStrategy in app/services/trading_engine.py"""
import pytest
from unittest.mock import MagicMock, patch

from app.services.trading_engine import (
    MomentumStrategy,
    MOMENTUM_WINDOW,
    MOMENTUM_THRESHOLD_BUY,
    MOMENTUM_THRESHOLD_SELL,
    TRADE_QUANTITY,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strategy_with_prices(symbol: str, prices: list) -> MomentumStrategy:
    """Return a strategy with a full price-history window for symbol."""
    s = MomentumStrategy()
    for p in prices:
        s.update_price_history(symbol, p)
    return s


# ---------------------------------------------------------------------------
# calculate_momentum
# ---------------------------------------------------------------------------

class TestCalculateMomentum:

    def test_returns_zero_with_single_price(self):
        s = MomentumStrategy()
        s.update_price_history("SPY", 100.0)
        assert s.calculate_momentum("SPY") == 0.0

    def test_returns_zero_for_unknown_symbol(self):
        s = MomentumStrategy()
        assert s.calculate_momentum("UNKNOWN") == 0.0

    def test_positive_momentum_when_price_rises(self):
        prices = [100.0, 101.0, 102.0, 103.0, 105.0]
        s = _strategy_with_prices("SPY", prices)
        momentum = s.calculate_momentum("SPY")
        # (105 - 100) / 100 = 0.05
        assert pytest.approx(momentum, rel=1e-6) == 0.05

    def test_negative_momentum_when_price_falls(self):
        prices = [100.0, 99.0, 98.0, 97.0, 95.0]
        s = _strategy_with_prices("SPY", prices)
        momentum = s.calculate_momentum("SPY")
        # (95 - 100) / 100 = -0.05
        assert pytest.approx(momentum, rel=1e-6) == -0.05

    def test_zero_momentum_for_flat_prices(self):
        prices = [100.0] * MOMENTUM_WINDOW
        s = _strategy_with_prices("SPY", prices)
        assert s.calculate_momentum("SPY") == 0.0

    def test_oldest_price_is_zero_returns_zero(self):
        s = MomentumStrategy()
        # Manually inject 0 as first price (edge case guard)
        for _ in range(MOMENTUM_WINDOW):
            s.update_price_history("SPY", 0.0)
        assert s.calculate_momentum("SPY") == 0.0

    def test_window_rolls_over_old_prices(self):
        """Only the MOMENTUM_WINDOW most recent prices should be used."""
        s = MomentumStrategy()
        # Fill window with 100, then add 200 — oldest should drop off
        for _ in range(MOMENTUM_WINDOW):
            s.update_price_history("SPY", 100.0)
        s.update_price_history("SPY", 200.0)
        # Oldest is now 100 (second value), current is 200
        # (200 - 100) / 100 = 1.0
        assert s.calculate_momentum("SPY") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# should_buy
# ---------------------------------------------------------------------------

class TestShouldBuy:

    def test_returns_false_without_full_window(self):
        s = MomentumStrategy()
        for _ in range(MOMENTUM_WINDOW - 1):  # one short
            s.update_price_history("SPY", 100.0)
        assert s.should_buy("SPY", 100.0) is False

    def test_returns_false_when_momentum_below_threshold(self):
        # Flat prices → momentum = 0, below buy threshold
        prices = [100.0] * MOMENTUM_WINDOW
        s = _strategy_with_prices("SPY", prices)
        assert s.should_buy("SPY", 100.0) is False

    def test_returns_true_when_momentum_above_threshold(self):
        # Build prices that produce momentum > MOMENTUM_THRESHOLD_BUY
        base = 100.0
        factor = 1 + MOMENTUM_THRESHOLD_BUY + 0.01  # slightly above threshold
        end_price = base * factor
        prices = [base] + [base] * (MOMENTUM_WINDOW - 2) + [end_price]
        s = _strategy_with_prices("SPY", prices)
        assert s.should_buy("SPY", end_price) is True

    def test_returns_false_when_recent_trade_too_close(self):
        """Don't buy again if price hasn't moved more than 1% since last trade."""
        base = 100.0
        factor = 1 + MOMENTUM_THRESHOLD_BUY + 0.01
        end_price = base * factor
        prices = [base] * (MOMENTUM_WINDOW - 1) + [end_price]
        s = _strategy_with_prices("SPY", prices)
        s.last_trade_price["SPY"] = end_price  # trade at same price
        assert s.should_buy("SPY", end_price) is False


# ---------------------------------------------------------------------------
# should_sell
# ---------------------------------------------------------------------------

class TestShouldSell:

    def test_returns_false_with_no_position(self):
        prices = [100.0, 99.0, 98.0, 97.0, 95.0]
        s = _strategy_with_prices("SPY", prices)
        # No position held
        assert s.should_sell("SPY", 95.0) is False

    def test_returns_false_when_momentum_above_threshold(self):
        prices = [100.0] * MOMENTUM_WINDOW  # flat = 0 momentum, not negative enough
        s = _strategy_with_prices("SPY", prices)
        s.positions["SPY"] = 2
        assert s.should_sell("SPY", 100.0) is False

    def test_returns_true_when_momentum_below_threshold_and_position_held(self):
        base = 100.0
        factor = 1 + MOMENTUM_THRESHOLD_SELL - 0.01  # below sell threshold (negative)
        end_price = base * factor
        prices = [base] * (MOMENTUM_WINDOW - 1) + [end_price]
        s = _strategy_with_prices("SPY", prices)
        s.positions["SPY"] = 1
        assert s.should_sell("SPY", end_price) is True


# ---------------------------------------------------------------------------
# execute_buy / execute_sell
# ---------------------------------------------------------------------------

class TestExecuteOrders:

    def _mock_client_success(self):
        mock = MagicMock()
        mock.place_order.return_value = {
            "order_id": "test-id",
            "symbol": "SPY",
            "quantity": TRADE_QUANTITY,
            "side": "buy",
            "type": "market",
            "status": "new",
            "asset_class": "stock",
        }
        return mock

    def test_execute_buy_increments_position(self):
        s = MomentumStrategy()
        with patch("app.services.trading_engine.alpaca_client", self._mock_client_success()):
            s.execute_buy("SPY", 100.0)
        assert s.positions["SPY"] == TRADE_QUANTITY

    def test_execute_buy_records_last_trade_price(self):
        s = MomentumStrategy()
        with patch("app.services.trading_engine.alpaca_client", self._mock_client_success()):
            s.execute_buy("SPY", 123.45)
        assert s.last_trade_price["SPY"] == 123.45

    def test_execute_buy_returns_false_when_order_fails(self):
        mock = MagicMock()
        mock.place_order.return_value = None  # broker rejected
        s = MomentumStrategy()
        with patch("app.services.trading_engine.alpaca_client", mock):
            result = s.execute_buy("SPY", 100.0)
        assert result is False
        assert s.positions["SPY"] == 0

    def test_execute_sell_decrements_position(self):
        s = MomentumStrategy()
        s.positions["SPY"] = TRADE_QUANTITY * 2

        mock = MagicMock()
        mock.place_order.return_value = {"order_id": "sell-id", "symbol": "SPY",
                                          "quantity": TRADE_QUANTITY, "side": "sell",
                                          "type": "market", "status": "new", "asset_class": "stock"}
        with patch("app.services.trading_engine.alpaca_client", mock):
            s.execute_sell("SPY", 110.0)
        assert s.positions["SPY"] == TRADE_QUANTITY  # reduced by one lot

    def test_execute_sell_with_no_position_returns_false(self):
        s = MomentumStrategy()
        with patch("app.services.trading_engine.alpaca_client", self._mock_client_success()):
            result = s.execute_sell("SPY", 100.0)
        assert result is False

    def test_get_status_returns_dict(self):
        s = MomentumStrategy()
        s.positions["SPY"] = 3
        status = s.get_status()
        assert "positions" in status
        assert status["positions"]["SPY"] == 3
