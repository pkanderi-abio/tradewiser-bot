"""Tests for /trades/* routes."""
import pytest
from unittest.mock import patch
from app.core.config import settings


# ---------------------------------------------------------------------------
# POST /trades/execute — live order
# ---------------------------------------------------------------------------

class TestExecuteTrade:

    def test_execute_returns_submitted(self, client, mock_alpaca):
        resp = client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "BUY",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "submitted"
        assert "data" in data
        assert "id" in data

    def test_execute_uppercases_symbol(self, client, mock_alpaca):
        client.post("/trades/execute", json={"symbol": "aapl", "quantity": 1, "side": "BUY"})
        mock_alpaca.place_order.assert_called_once()
        call_kwargs = mock_alpaca.place_order.call_args
        assert call_kwargs[1]["symbol"] == "AAPL" or call_kwargs[0][0] == "AAPL"

    def test_execute_returns_503_when_broker_fails(self, client, mock_alpaca):
        mock_alpaca.place_order.return_value = None
        resp = client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "BUY",
        })
        assert resp.status_code == 503

    def test_execute_requires_positive_quantity(self, client, mock_alpaca):
        resp = client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 0,
            "side": "BUY",
        })
        assert resp.status_code == 422  # pydantic validation error

    def test_execute_requires_price_for_limit_order(self, client, mock_alpaca):
        resp = client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "BUY",
            "order_type": "LMT",
            # price missing
        })
        assert resp.status_code == 422

    def test_execute_limit_order_with_price(self, client, mock_alpaca):
        resp = client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "BUY",
            "order_type": "LMT",
            "price": 148.50,
        })
        assert resp.status_code == 200

    def test_execute_all_valid_sides(self, client, mock_alpaca):
        for side in ("BUY", "SELL", "SHORT"):
            resp = client.post("/trades/execute", json={
                "symbol": "AAPL",
                "quantity": 1,
                "side": side,
            })
            assert resp.status_code == 200, f"Side {side} should be valid"

    def test_execute_invalid_side_rejected(self, client, mock_alpaca):
        resp = client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "HOLD",
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /trades/execute — dry run
# ---------------------------------------------------------------------------

class TestDryRun:

    def test_dry_run_returns_dry_run_status(self, client, mock_alpaca):
        resp = client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "BUY",
            "dry_run": True,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "dry_run"

    def test_dry_run_does_not_call_place_order(self, client, mock_alpaca):
        client.post("/trades/execute", json={
            "symbol": "AAPL",
            "quantity": 1,
            "side": "BUY",
            "dry_run": True,
        })
        mock_alpaca.place_order.assert_not_called()

    def test_dry_run_response_contains_order_details(self, client, mock_alpaca):
        resp = client.post("/trades/execute", json={
            "symbol": "TSLA",
            "quantity": 5,
            "side": "SELL",
            "dry_run": True,
        })
        data = resp.json()
        assert data["order"]["symbol"] == "TSLA"
        assert data["order"]["quantity"] == 5


# ---------------------------------------------------------------------------
# GET /trades/audit
# ---------------------------------------------------------------------------

class TestAuditLog:

    def test_audit_returns_200(self, client, mock_alpaca):
        resp = client.get("/trades/audit")
        assert resp.status_code == 200

    def test_audit_returns_list(self, client, mock_alpaca):
        data = client.get("/trades/audit").json()
        assert "audit" in data
        assert isinstance(data["audit"], list)

    def test_audit_records_executed_trades(self, client, mock_alpaca):
        # Submit an order so something appears in the log
        client.post("/trades/execute", json={"symbol": "SPY", "quantity": 2, "side": "BUY"})
        data = client.get("/trades/audit").json()
        symbols = [e["symbol"] for e in data["audit"]]
        assert "SPY" in symbols

    def test_audit_limit_param(self, client, mock_alpaca):
        # Submit 3 orders
        for _ in range(3):
            client.post("/trades/execute", json={"symbol": "QQQ", "quantity": 1, "side": "BUY"})
        data = client.get("/trades/audit?limit=2").json()
        assert len(data["audit"]) <= 2


# ---------------------------------------------------------------------------
# GET /trades/current
# ---------------------------------------------------------------------------

class TestCurrentOrders:

    def test_current_orders_returns_200(self, client, mock_alpaca):
        resp = client.get("/trades/current")
        assert resp.status_code == 200

    def test_current_orders_returns_list(self, client, mock_alpaca):
        data = client.get("/trades/current").json()
        assert "orders" in data
        assert isinstance(data["orders"], list)

    def test_current_orders_returns_503_when_broker_fails(self, client, mock_alpaca):
        mock_alpaca.get_current_orders.return_value = None
        resp = client.get("/trades/current")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /trades/strategy/status
# ---------------------------------------------------------------------------

class TestStrategyStatus:

    def test_strategy_status_returns_200(self, client, mock_alpaca):
        resp = client.get("/trades/strategy/status")
        assert resp.status_code == 200

    def test_strategy_status_contains_parameters(self, client, mock_alpaca):
        data = client.get("/trades/strategy/status").json()
        assert "parameters" in data
        assert "window" in data["parameters"]
        assert "buy_threshold" in data["parameters"]
        assert "sell_threshold" in data["parameters"]

    def test_strategy_status_contains_positions(self, client, mock_alpaca):
        data = client.get("/trades/strategy/status").json()
        assert "positions" in data


# ---------------------------------------------------------------------------
# Auth enforcement on trades
# ---------------------------------------------------------------------------

class TestTradesAuth:

    def test_execute_returns_401_without_key(self, authed_client, mock_alpaca):
        c, _ = authed_client
        resp = c.post("/trades/execute", json={"symbol": "AAPL", "quantity": 1, "side": "BUY"})
        assert resp.status_code == 401

    def test_execute_returns_200_with_correct_key(self, authed_client, mock_alpaca):
        c, key = authed_client
        resp = c.post(
            "/trades/execute",
            json={"symbol": "AAPL", "quantity": 1, "side": "BUY"},
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200

    def test_audit_returns_401_without_key(self, authed_client, mock_alpaca):
        c, _ = authed_client
        assert c.get("/trades/audit").status_code == 401

    def test_strategy_status_returns_401_without_key(self, authed_client, mock_alpaca):
        c, _ = authed_client
        assert c.get("/trades/strategy/status").status_code == 401
