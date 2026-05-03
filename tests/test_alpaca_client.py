"""Tests for AlpacaClient (app/services/alpaca_client.py)."""
import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


def _fresh_client(mock_trading_cls, mock_data_cls=None):
    """Import AlpacaClient with a clean module (no cached singleton)."""
    for mod in list(sys.modules.keys()):
        if "alpaca_client" in mod:
            del sys.modules[mod]

    patches = {"alpaca.trading.client.TradingClient": mock_trading_cls}
    if mock_data_cls:
        patches["alpaca.data.historical.StockHistoricalDataClient"] = mock_data_cls
        patches["alpaca.data.historical.OptionHistoricalDataClient"] = mock_data_cls

    with patch.dict("sys.modules", {}):
        with patch("alpaca.trading.client.TradingClient", mock_trading_cls):
            mod = importlib.import_module("app.services.alpaca_client")
            return mod.AlpacaClient()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:

    def _make_client(self, get_account_side_effect=None, get_account_return=None):
        mock_tc_cls = MagicMock()
        mock_tc = mock_tc_cls.return_value
        if get_account_side_effect:
            mock_tc.get_account.side_effect = get_account_side_effect
        elif get_account_return is not None:
            mock_tc.get_account.return_value = get_account_return
        else:
            mock_tc.get_account.return_value = MagicMock(id="acc-123")

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls):
            mod = importlib.import_module("app.services.alpaca_client")
            client = mod.AlpacaClient()
            client._trading_client_mock = mock_tc
            return client

    def test_successful_auth_sets_authenticated_true(self):
        client = self._make_client()
        assert client.login() is True
        assert client.authenticated is True
        assert client._auth_failed is False

    def test_successful_auth_stores_account_id(self):
        client = self._make_client()
        client.login()
        assert client._account_id == "acc-123"

    def test_auth_failure_sets_auth_failed_flag(self):
        client = self._make_client(
            get_account_side_effect=Exception("connection refused")
        )
        result = client.login()
        assert result is False
        assert client.authenticated is False
        assert client._auth_failed is True

    def test_auth_failure_blocks_all_subsequent_calls(self):
        """After a failure, _ensure_authenticated must not retry (avoids API hammering)."""
        call_count = {"n": 0}

        def failing_get_account():
            call_count["n"] += 1
            raise Exception("always fails")

        client = self._make_client(get_account_side_effect=failing_get_account)

        client.login()          # first attempt
        client.login()          # should short-circuit, NOT call get_account again
        client.login()          # same

        assert call_count["n"] == 1, (
            "get_account() should only be called once; subsequent calls must short-circuit"
        )

    def test_already_authenticated_skips_get_account(self):
        client = self._make_client()
        client.login()  # authenticates
        call_count_after = client._trading_client_mock.get_account.call_count

        client.login()  # should NOT call get_account again
        assert client._trading_client_mock.get_account.call_count == call_count_after


# ---------------------------------------------------------------------------
# get_quote
# ---------------------------------------------------------------------------

class TestGetQuote:

    def _make_authenticated_client(self):
        mock_tc_cls = MagicMock()
        mock_tc = mock_tc_cls.return_value
        mock_tc.get_account.return_value = MagicMock(id="acc-1")

        mock_quote = MagicMock()
        mock_quote.ask_price = 150.10
        mock_quote.bid_price = 149.90
        mock_quote.ask_size = 100
        mock_quote.bid_size = 100

        mock_data_cls = MagicMock()
        mock_data = mock_data_cls.return_value
        mock_data.get_stock_latest_quote.return_value = {"AAPL": mock_quote}

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", mock_data_cls), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
            mod = importlib.import_module("app.services.alpaca_client")
            client = mod.AlpacaClient()
            client.login()
            return client

    def test_get_stock_quote_returns_dict(self):
        client = self._make_authenticated_client()
        result = client.get_quote("AAPL")
        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["source"] == "alpaca"

    def test_get_quote_falls_back_to_yfinance_on_auth_failure(self):
        mock_tc_cls = MagicMock()
        mock_tc_cls.return_value.get_account.side_effect = Exception("auth failed")

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        mock_ticker = MagicMock()
        mock_ticker.info = {
            "currentPrice": 145.0,
            "bid": 144.9,
            "ask": 145.1,
            "volume": 500,
        }

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()), \
             patch("yfinance.Ticker", return_value=mock_ticker):
            mod = importlib.import_module("app.services.alpaca_client")
            client = mod.AlpacaClient()
            result = client.get_quote("AAPL")

        assert result is not None
        assert result["source"] == "yfinance_fallback"
        assert result["pLast"] == 145.0


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

class TestPlaceOrder:

    def test_place_order_returns_none_when_not_authenticated(self):
        mock_tc_cls = MagicMock()
        mock_tc_cls.return_value.get_account.side_effect = Exception("no auth")

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
            mod = importlib.import_module("app.services.alpaca_client")
            client = mod.AlpacaClient()
            result = client.place_order("AAPL", 1, "BUY")

        assert result is None

    def test_place_order_returns_order_dict_on_success(self):
        mock_tc_cls = MagicMock()
        mock_tc = mock_tc_cls.return_value
        mock_tc.get_account.return_value = MagicMock(id="acc-1")

        mock_order = MagicMock(
            id="ord-99",
            symbol="AAPL",
            qty=2,
            side=MagicMock(value="buy"),
            type=MagicMock(value="market"),
            status=MagicMock(value="new"),
        )
        mock_tc.submit_order.return_value = mock_order

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
            mod = importlib.import_module("app.services.alpaca_client")
            client = mod.AlpacaClient()
            result = client.place_order("AAPL", 2, "BUY")

        assert result is not None
        assert result["order_id"] == "ord-99"
        assert result["symbol"] == "AAPL"
        assert result["quantity"] == 2


# ---------------------------------------------------------------------------
# get_current_orders
# ---------------------------------------------------------------------------

class TestGetCurrentOrders:

    def test_returns_order_list_when_authenticated(self):
        mock_tc_cls = MagicMock()
        mock_tc = mock_tc_cls.return_value
        mock_tc.get_account.return_value = MagicMock(id="acc-123")

        mock_order = MagicMock(
            id="ord-1",
            symbol="AAPL",
            qty=1,
            side=MagicMock(value="buy"),
            type=MagicMock(value="market"),
            status=MagicMock(value="new"),
            submitted_at=MagicMock(isoformat=MagicMock(return_value="2026-04-27T00:00:00")),
        )
        mock_tc.get_orders.return_value = [mock_order]

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
            mod = importlib.import_module("app.services.alpaca_client")
            client = mod.AlpacaClient()
            result = client.get_current_orders()

        assert result == [{
            "order_id": "ord-1",
            "symbol": "AAPL",
            "quantity": 1,
            "side": "buy",
            "type": "market",
            "status": "new",
            "submitted_at": "2026-04-27T00:00:00",
        }]

    def test_returns_none_when_not_authenticated(self):
        mock_tc_cls = MagicMock()
        mock_tc_cls.return_value.get_account.side_effect = Exception("no auth")

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
            mod = importlib.import_module("app.services.alpaca_client")
            client = mod.AlpacaClient()
            result = client.get_current_orders()

        assert result is None
