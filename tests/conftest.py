"""
Shared pytest fixtures for the TradeWiser test suite.

Environment variables are set at import time (before app modules are loaded)
so pydantic-settings picks them up when Settings() is first constructed.
"""
import asyncio
import os

# Must be set before any app module is imported
os.environ.setdefault("ALPACA_API_KEY", "test_key_id")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret_key")
os.environ.setdefault("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
os.environ.setdefault("BOT_API_KEY", "test-bot-api-key-for-pytest")
# In-memory SQLite keeps the trade_audit / ai_decisions tables isolated to the
# pytest process — production uses a file on disk via AI_AUDIT_DB_PATH.
os.environ.setdefault("AI_AUDIT_DB_PATH", ":memory:")

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

TEST_API_KEY = "test-bot-api-key-for-pytest"


async def _idle_trading_loop():
    """Replaces start_trading_loop in tests — sleeps until cancelled."""
    try:
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass


@pytest.fixture
def client():
    """Authenticated TestClient — sends TEST_API_KEY on every request."""
    with patch("app.main.start_trading_loop", _idle_trading_loop):
        from app.main import app
        with TestClient(app, headers={"X-API-Key": TEST_API_KEY}) as c:
            yield c


@pytest.fixture
def authed_client():
    """TestClient with BOT_API_KEY enforced.

    Yields a (client, api_key) tuple so tests can supply the correct header.
    """
    with patch("app.main.start_trading_loop", _idle_trading_loop):
        from app.main import app
        from app.core.config import settings
        with patch.object(settings, "BOT_API_KEY", TEST_API_KEY):
            with TestClient(app) as c:
                yield c, TEST_API_KEY


@pytest.fixture
def mock_alpaca():
    """Patches the Alpaca client singleton with a MagicMock.

    Use this fixture in route tests to avoid real Alpaca API calls.
    """
    mock = MagicMock()
    mock.login.return_value = True
    mock.get_quote.return_value = {
        "symbol": "AAPL",
        "pLast": 150.00,
        "bid": 149.99,
        "ask": 150.01,
        "volume": 1000,
        "source": "alpaca",
    }
    mock.place_order.return_value = {
        "order_id": "test-order-id",
        "symbol": "AAPL",
        "quantity": 1,
        "side": "buy",
        "type": "market",
        "status": "new",
        "asset_class": "stock",
    }
    mock.get_current_orders.return_value = []
    mock.get_order_history.return_value = []

    with patch("app.routes.trades.alpaca_client", mock), \
         patch("app.routes.quotes.alpaca_client", mock):
        yield mock
