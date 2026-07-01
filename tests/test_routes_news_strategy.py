"""Routes tests for /trades/news-strategy, /trades/news-strategy/positions,
and /trades/news-events."""
from __future__ import annotations

import pytest


TEST_API_KEY = "test-bot-api-key-for-pytest"


@pytest.fixture(autouse=True)
def _reset_tables():
    from app.services import utils as utils_mod
    utils_mod.truncate_tables_for_tests(
        "multi_day_positions", "news_events", "trade_audit", "risk_events",
    )


class TestNewsStrategyStatus:
    def test_shape(self, client):
        r = client.get("/trades/news-strategy")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "strategy" in body and "extractor" in body
        assert "calibration" in body and "audit_stats" in body
        assert "open_positions" in body

    def test_returns_open_positions(self, client):
        from app.services.position_manager import position_manager
        p = position_manager.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        position_manager.record_fill(p.id, fill_price=100.0, shares=10)
        r = client.get("/trades/news-strategy")
        body = r.json()
        assert len(body["open_positions"]) == 1
        assert body["open_positions"][0]["symbol"] == "NVDA"
        assert body["open_positions"][0]["entry_price"] == 100.0

    def test_window_days_param_flows_to_calibration(self, client):
        r = client.get("/trades/news-strategy?window_days=7")
        assert r.json()["calibration"]["window_days"] == 7

    def test_requires_api_key(self):
        # Build a bare TestClient without the auto-injected key.
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        with patch("app.main.start_trading_loop"):
            from app.main import app
            with TestClient(app) as no_key:
                r = no_key.get("/trades/news-strategy")
        assert r.status_code in (401, 403, 422)


class TestNewsStrategyPositions:
    def test_lists_positions(self, client):
        from app.services.position_manager import position_manager
        p1 = position_manager.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        position_manager.record_fill(p1.id, fill_price=100.0, shares=10)
        p2 = position_manager.open_position(
            strategy="news_event_v1", symbol="TSLA", underlying="TSLA",
            instrument="stock", entry_severity=6.0, entry_event_type="product_launch",
        )
        r = client.get("/trades/news-strategy/positions")
        assert r.status_code == 200
        body = r.json()
        assert body["n"] == 2
        symbols = {p["symbol"] for p in body["positions"]}
        assert symbols == {"NVDA", "TSLA"}

    def test_state_filter(self, client):
        from app.services.position_manager import position_manager
        p1 = position_manager.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        position_manager.record_fill(p1.id, fill_price=100.0, shares=10)
        position_manager.open_position(
            strategy="news_event_v1", symbol="TSLA", underlying="TSLA",
            instrument="stock", entry_severity=6.0, entry_event_type="product_launch",
        )
        open_only = client.get("/trades/news-strategy/positions?state=open").json()
        pending_only = client.get("/trades/news-strategy/positions?state=pending").json()
        assert {p["symbol"] for p in open_only["positions"]} == {"NVDA"}
        assert {p["symbol"] for p in pending_only["positions"]} == {"TSLA"}


class TestNewsEventsEndpoint:
    def test_returns_recent_events(self, client):
        from app.services.utils import record_news_event
        record_news_event({
            "symbol": "NVDA", "headline_hash": "h1",
            "headline": "Nvidia beats earnings",
            "event_type": "earnings_beat", "severity": 8, "confidence": 0.9,
            "reason": "beat", "provider": "groq", "model": "llama-3.3-70b-versatile",
            "outcome": "ok", "attempts": 1, "prompt_hash": "abc",
        })
        r = client.get("/trades/news-events?symbol=NVDA")
        events = r.json()["events"]
        assert len(events) == 1 and events[0]["event_type"] == "earnings_beat"

    def test_default_only_returns_ok_outcome(self, client):
        from app.services.utils import record_news_event
        record_news_event({
            "symbol": "NVDA", "headline_hash": "h_ok",
            "headline": "ok headline", "event_type": "upgrade",
            "severity": 5, "confidence": 0.8, "reason": "",
            "provider": "groq", "model": "m", "outcome": "ok", "attempts": 1,
        })
        record_news_event({
            "symbol": "NVDA", "headline_hash": "h_fail",
            "headline": "fail headline", "event_type": "other",
            "severity": 0, "confidence": 0.0, "reason": "",
            "provider": "groq", "model": "m", "outcome": "schema_error", "attempts": 3,
        })
        default = client.get("/trades/news-events").json()["events"]
        assert all(e["outcome"] == "ok" for e in default)
