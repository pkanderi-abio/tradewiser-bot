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
# Auth retry / cooldown — the 2026-05-12 regression
# ---------------------------------------------------------------------------

class TestAuthCooldown:
    """The bot was silently down for 27 days because a single startup auth
    failure latched _auth_failed forever. These tests pin the new contract:

      - within ALPACA_AUTH_RETRY_COOLDOWN_SECONDS of a failure, repeated
        login() calls short-circuit (don't hammer Alpaca).
      - after the cooldown elapses, login() retries from scratch and can
        recover without a service restart.
    """

    def _make_client_with_account(self, get_account_mock):
        mock_tc_cls = MagicMock()
        mock_tc = mock_tc_cls.return_value
        mock_tc.get_account = get_account_mock

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
            mod = importlib.import_module("app.services.alpaca_client")
            return mod.AlpacaClient(), mock_tc

    def test_retry_blocked_inside_cooldown(self):
        """Three rapid retries within the cooldown window only call get_account once."""
        mock = MagicMock(side_effect=Exception("network blip"))
        client, _ = self._make_client_with_account(mock)
        for _ in range(3):
            client.login()
        assert mock.call_count == 1
        assert client._auth_failed is True
        assert client._consecutive_auth_failures == 1  # only the first attempt counted

    def test_retry_allowed_after_cooldown(self, monkeypatch):
        """When the cooldown elapses, a second login() re-attempts and can succeed."""
        from app.core.config import settings
        # Tiny cooldown so the test is fast.
        monkeypatch.setattr(settings, "ALPACA_AUTH_RETRY_COOLDOWN_SECONDS", 0)

        # First call fails, second succeeds — simulating a transient outage that recovers.
        calls = {"n": 0}
        def flaky_get_account():
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("transient")
            return MagicMock(id="acc-recovered")

        client, _ = self._make_client_with_account(MagicMock(side_effect=flaky_get_account))

        assert client.login() is False
        assert client._auth_failed is True
        # Cooldown is 0 — next call should retry
        assert client.login() is True
        assert client.authenticated is True
        assert client._account_id == "acc-recovered"
        assert client._auth_failed is False
        assert client._consecutive_auth_failures == 0  # reset on success
        assert calls["n"] == 2

    def test_consecutive_failures_accumulate(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "ALPACA_AUTH_RETRY_COOLDOWN_SECONDS", 0)

        mock = MagicMock(side_effect=Exception("still down"))
        client, _ = self._make_client_with_account(mock)
        client.login()
        client.login()
        client.login()
        assert client._consecutive_auth_failures == 3
        assert client._auth_failed is True
        assert "still down" in (client._last_auth_error or "")

    def test_last_auth_error_captured(self):
        mock = MagicMock(side_effect=Exception("401 Unauthorized"))
        client, _ = self._make_client_with_account(mock)
        client.login()
        assert client._last_auth_error is not None
        assert "401" in client._last_auth_error


# ---------------------------------------------------------------------------
# broker_snapshot() — surfaces what the /health/broker endpoint shows
# ---------------------------------------------------------------------------

class TestBrokerSnapshot:

    def _client(self, get_account_side_effect=None, get_account_return=None):
        mock_tc_cls = MagicMock()
        mock_tc = mock_tc_cls.return_value
        if get_account_side_effect:
            mock_tc.get_account.side_effect = get_account_side_effect
        else:
            mock_tc.get_account.return_value = get_account_return or MagicMock(id="acc-snap")

        for mod in list(sys.modules.keys()):
            if "alpaca_client" in mod:
                del sys.modules[mod]

        with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
             patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
             patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
            mod = importlib.import_module("app.services.alpaca_client")
            return mod.AlpacaClient()

    def test_snapshot_before_any_auth(self):
        c = self._client()
        snap = c.broker_snapshot()
        assert snap["authenticated"] is False
        assert snap["auth_failed_latched"] is False
        assert snap["consecutive_failures"] == 0
        assert snap["last_attempt_age_seconds"] is None
        assert snap["last_success_age_seconds"] is None
        assert "base_url" in snap
        assert "paper_mode" in snap

    def test_snapshot_after_successful_auth(self):
        c = self._client()
        c.login()
        snap = c.broker_snapshot()
        assert snap["authenticated"] is True
        assert snap["auth_failed_latched"] is False
        assert snap["account_id"] == "acc-snap"
        assert snap["last_success_age_seconds"] is not None
        assert snap["last_attempt_age_seconds"] is not None

    def test_snapshot_after_auth_failure_shows_cooldown(self):
        c = self._client(get_account_side_effect=Exception("network down"))
        c.login()
        snap = c.broker_snapshot()
        assert snap["authenticated"] is False
        assert snap["auth_failed_latched"] is True
        assert snap["last_error"] is not None
        assert snap["last_attempt_age_seconds"] is not None
        # seconds_until_retry must be > 0 right after a failure
        assert snap["seconds_until_retry"] is not None
        assert snap["seconds_until_retry"] >= 0


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
