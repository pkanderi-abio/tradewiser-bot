"""Tests for AlpacaClient (app/services/alpaca_client.py)."""
import importlib
import sys
import time
from datetime import datetime, timedelta, timezone
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


# ---------------------------------------------------------------------------
# Market-hours gate + stale-order reaper — 2026-07-18 AXP-lockup regression
# ---------------------------------------------------------------------------

def _client_with_trading_mock(trading_mock=None):
    """Build an AlpacaClient with a pre-configured TradingClient mock and
    a successful default get_account so the client is authenticated."""
    mock_tc_cls = MagicMock()
    mock_tc = trading_mock or mock_tc_cls.return_value
    if trading_mock:
        mock_tc_cls.return_value = trading_mock
    mock_tc.get_account.return_value = MagicMock(id="acc-123")

    for mod in list(sys.modules.keys()):
        if "alpaca_client" in mod:
            del sys.modules[mod]

    with patch("alpaca.trading.client.TradingClient", mock_tc_cls), \
         patch("alpaca.data.historical.StockHistoricalDataClient", MagicMock()), \
         patch("alpaca.data.historical.OptionHistoricalDataClient", MagicMock()):
        mod = importlib.import_module("app.services.alpaca_client")
        client = mod.AlpacaClient()
        client.login()          # populate self.authenticated
        client._trading_mock = mock_tc
        return client


class TestMarketClock:
    """market_clock() is what the trading loop reads to skip evaluation on
    closed sessions. It must cache (Alpaca's /v2/clock rarely changes) and
    it must fail open — a broker outage that returns None is 'assume open',
    not 'silently halt trading'."""

    def test_returns_clock_payload_on_success(self):
        client = _client_with_trading_mock()
        clock = MagicMock(
            is_open=True,
            next_open="2026-07-20T13:30:00Z",
            next_close="2026-07-20T20:00:00Z",
            timestamp="2026-07-18T21:00:00Z",
        )
        client._trading_mock.get_clock.return_value = clock

        result = client.market_clock()
        assert result == {
            "is_open": True,
            "next_open": "2026-07-20T13:30:00Z",
            "next_close": "2026-07-20T20:00:00Z",
            "timestamp": "2026-07-18T21:00:00Z",
        }

    def test_cached_within_ttl(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "MARKET_CLOCK_CACHE_SECONDS", 60)
        client = _client_with_trading_mock()
        client._trading_mock.get_clock.return_value = MagicMock(is_open=True)

        for _ in range(5):
            client.market_clock()

        # 5 rapid calls, only 1 hits the broker.
        assert client._trading_mock.get_clock.call_count == 1

    def test_refetches_after_ttl(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "MARKET_CLOCK_CACHE_SECONDS", 0)
        client = _client_with_trading_mock()
        client._trading_mock.get_clock.return_value = MagicMock(is_open=True)

        client.market_clock()
        client.market_clock()

        # TTL=0 disables the cache — every call hits the broker.
        assert client._trading_mock.get_clock.call_count == 2

    def test_returns_none_on_broker_error_fails_open(self):
        """Fail-open contract: broker error → None → caller assumes market is
        open. Never silently halt trading on a clock hiccup."""
        client = _client_with_trading_mock()
        client._trading_mock.get_clock.side_effect = RuntimeError("500")

        assert client.market_clock() is None

    def test_returns_none_when_not_authenticated(self):
        client = _client_with_trading_mock()
        client.authenticated = False
        client._auth_failed = True
        client._last_auth_attempt = time.time()

        assert client.market_clock() is None
        client._trading_mock.get_clock.assert_not_called()


class TestCancelStaleOpenOrders:
    """The reaper. On 2026-07-18 an AXP option SELL sat queued all weekend
    because a bad limit missed by a cent — the position was locked because
    execute_sell defers when has_open_order is True. This method ages out
    those stale orders so the next execute_sell can re-submit fresh."""

    def _order(self, order_id, submitted_at, side="sell"):
        m = MagicMock()
        m.id = order_id
        m.submitted_at = submitted_at
        m.created_at = submitted_at
        m.side = MagicMock(value=side)
        return m

    def test_zero_age_disables_reaper(self):
        client = _client_with_trading_mock()

        assert client.cancel_stale_open_orders("AAPL250117C00150000", 0) == 0
        client._trading_mock.get_orders.assert_not_called()
        client._trading_mock.cancel_order_by_id.assert_not_called()

    def test_no_open_orders_returns_zero(self):
        client = _client_with_trading_mock()
        client._trading_mock.get_orders.return_value = []

        assert client.cancel_stale_open_orders("AAPL250117C00150000", 30) == 0
        client._trading_mock.cancel_order_by_id.assert_not_called()

    def test_fresh_orders_are_not_cancelled(self):
        client = _client_with_trading_mock()
        now = datetime.now(timezone.utc)
        # 5min old — well inside the 30min cutoff
        fresh = self._order("ord-fresh", now - timedelta(minutes=5))
        client._trading_mock.get_orders.return_value = [fresh]

        assert client.cancel_stale_open_orders("AAPL250117C00150000", 30) == 0
        client._trading_mock.cancel_order_by_id.assert_not_called()

    def test_stale_orders_are_cancelled(self):
        client = _client_with_trading_mock()
        now = datetime.now(timezone.utc)
        stale = self._order("ord-stale", now - timedelta(hours=48))  # weekend-old
        fresh = self._order("ord-fresh", now - timedelta(minutes=2))
        client._trading_mock.get_orders.return_value = [stale, fresh]

        cancelled = client.cancel_stale_open_orders("AAPL250117C00150000", 30)
        assert cancelled == 1
        client._trading_mock.cancel_order_by_id.assert_called_once_with("ord-stale")

    def test_strips_option_prefix_before_query(self):
        client = _client_with_trading_mock()
        client._trading_mock.get_orders.return_value = []

        client.cancel_stale_open_orders("O:AAPL250117C00150000", 30)

        req = client._trading_mock.get_orders.call_args.kwargs["filter"]
        assert req.symbols == ["AAPL250117C00150000"]

    def test_cancel_failure_does_not_halt_others(self):
        """One order failing to cancel shouldn't leave the others stuck."""
        client = _client_with_trading_mock()
        now = datetime.now(timezone.utc)
        stale_a = self._order("ord-a", now - timedelta(hours=48))
        stale_b = self._order("ord-b", now - timedelta(hours=48))
        client._trading_mock.get_orders.return_value = [stale_a, stale_b]
        client._trading_mock.cancel_order_by_id.side_effect = [
            RuntimeError("not found"), None,
        ]

        cancelled = client.cancel_stale_open_orders("AAPL250117C00150000", 30)
        assert cancelled == 1  # only the second succeeded, but the first didn't abort us
        assert client._trading_mock.cancel_order_by_id.call_count == 2

    def test_list_failure_returns_zero_fails_open(self):
        """Broker error listing orders returns 0 so execute_sell falls through
        to the existing defer path — never blocks on a broker hiccup."""
        client = _client_with_trading_mock()
        client._trading_mock.get_orders.side_effect = RuntimeError("500")

        assert client.cancel_stale_open_orders("AAPL250117C00150000", 30) == 0

    def test_naive_datetime_is_treated_as_utc(self):
        """Some broker responses have historically dropped tzinfo; the reaper
        must not crash comparing tz-naive vs tz-aware datetimes."""
        client = _client_with_trading_mock()
        now = datetime.now(timezone.utc)
        naive_stale = self._order(
            "ord-naive",
            (now - timedelta(hours=1)).replace(tzinfo=None),
        )
        client._trading_mock.get_orders.return_value = [naive_stale]

        cancelled = client.cancel_stale_open_orders("AAPL250117C00150000", 30)
        assert cancelled == 1
