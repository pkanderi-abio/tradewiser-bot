"""
Risk gate tests — verify the pre-trade portfolio safety checks.

The gate runs AFTER the AI advisor approves and BEFORE order placement. It
reads live state from alpaca_client (mocked here) plus persisted peak equity
from SQLite. Tests cover every breach path + the fail-open broker case +
the disabled-gate passthrough.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings


@pytest.fixture
def gate(monkeypatch):
    """Fresh RiskGate with default thresholds, gate enabled, and clean tables.

    Truncates risk_events + account_snapshots so peak-equity drawdown
    calculations don't pick up state from earlier tests.
    """
    from app.services.risk_gate import RiskGate
    from app.services.utils import truncate_tables_for_tests
    truncate_tables_for_tests("risk_events", "account_snapshots")
    monkeypatch.setattr(settings, "RISK_GATE_ENABLED", True)
    monkeypatch.setattr(settings, "RISK_MAX_SYMBOL_CONCENTRATION_PCT", 25.0)
    monkeypatch.setattr(settings, "RISK_MAX_DAILY_LOSS_DOLLARS", 500.0)
    monkeypatch.setattr(settings, "RISK_MAX_DRAWDOWN_PCT", 15.0)
    monkeypatch.setattr(settings, "RISK_PEAK_EQUITY_WINDOW_DAYS", 30)
    monkeypatch.setattr(settings, "RISK_HALT_BLOCKS_SELLS", False)
    return RiskGate()


def _patch_broker(monkeypatch, account: dict | None, positions: list):
    """Patch the singleton alpaca_client that risk_gate imported."""
    monkeypatch.setattr(
        "app.services.risk_gate.alpaca_client.get_account_pnl",
        lambda: account,
    )
    monkeypatch.setattr(
        "app.services.risk_gate.alpaca_client.get_positions_pnl",
        lambda: positions,
    )


# ── Disabled / broker-unavailable paths ──────────────────────────────────────

class TestPassthrough:
    def test_disabled_gate_approves(self, gate, monkeypatch):
        monkeypatch.setattr(settings, "RISK_GATE_ENABLED", False)
        decision = gate.evaluate("AAPL", "BUY", 1000.0)
        assert decision.approved is True
        assert "disabled" in decision.reason

    def test_broker_outage_fails_open(self, gate, monkeypatch):
        _patch_broker(monkeypatch, account=None, positions=[])
        decision = gate.evaluate("AAPL", "BUY", 1000.0)
        assert decision.approved is True
        assert "broker" in decision.reason


# ── Concentration ────────────────────────────────────────────────────────────

class TestConcentration:
    def test_under_cap_approved(self, gate, monkeypatch):
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": 0, "unrealized_pl": 0, "cash": 50_000, "buying_power": 100_000},
            positions=[],
        )
        decision = gate.evaluate("AAPL", "BUY", 1000.0)  # 1% of equity
        assert decision.approved is True

    def test_over_cap_blocked(self, gate, monkeypatch):
        # Existing AAPL position $20k + new $10k notional = $30k / $100k = 30% > 25% cap
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": 0, "unrealized_pl": 0, "cash": 50_000, "buying_power": 100_000},
            positions=[
                {"symbol": "AAPL", "market_value": 20_000, "qty": 100,
                 "side": "long", "asset_class": "us_equity",
                 "avg_entry_price": 200, "current_price": 200,
                 "cost_basis": 20_000, "unrealized_pl": 0, "unrealized_plpc": 0,
                 "unrealized_intraday_pl": 0, "unrealized_intraday_plpc": 0},
            ],
        )
        decision = gate.evaluate("AAPL", "BUY", 10_000.0)
        assert decision.approved is False
        assert any("concentration" in b for b in decision.breaches)

    def test_option_symbol_matched_to_underlying(self, gate, monkeypatch):
        # Holding an AAPL OPTION should count toward AAPL concentration when buying more AAPL exposure.
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": 0, "unrealized_pl": 0, "cash": 50_000, "buying_power": 100_000},
            positions=[
                {"symbol": "AAPL250117C00185000", "market_value": 22_000, "qty": 1,
                 "side": "long", "asset_class": "us_option",
                 "avg_entry_price": 22000, "current_price": 22000,
                 "cost_basis": 22_000, "unrealized_pl": 0, "unrealized_plpc": 0,
                 "unrealized_intraday_pl": 0, "unrealized_intraday_plpc": 0},
            ],
        )
        decision = gate.evaluate("AAPL", "BUY", 5_000.0)  # 22k + 5k = 27% > 25% cap
        assert decision.approved is False
        assert any("concentration" in b for b in decision.breaches)

    def test_sell_skips_concentration_check(self, gate, monkeypatch):
        # Even with a giant existing AAPL position, SELL should pass concentration
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": 0, "unrealized_pl": 0, "cash": 0, "buying_power": 0},
            positions=[
                {"symbol": "AAPL", "market_value": 99_000, "qty": 100,
                 "side": "long", "asset_class": "us_equity",
                 "avg_entry_price": 990, "current_price": 990,
                 "cost_basis": 99_000, "unrealized_pl": 0, "unrealized_plpc": 0,
                 "unrealized_intraday_pl": 0, "unrealized_intraday_plpc": 0},
            ],
        )
        decision = gate.evaluate("AAPL", "SELL", 0.0)
        assert decision.approved is True


# ── Daily loss ──────────────────────────────────────────────────────────────

class TestDailyLoss:
    def test_under_floor_blocked_on_buy(self, gate, monkeypatch):
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": -400, "unrealized_pl": -200,
                     "cash": 50_000, "buying_power": 100_000},
            positions=[],
        )
        decision = gate.evaluate("AAPL", "BUY", 1000.0)
        assert decision.approved is False
        assert any("daily_loss" in b for b in decision.breaches)

    def test_sells_pass_by_default(self, gate, monkeypatch):
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": -400, "unrealized_pl": -200,
                     "cash": 50_000, "buying_power": 100_000},
            positions=[],
        )
        decision = gate.evaluate("AAPL", "SELL", 0.0)
        assert decision.approved is True

    def test_halt_blocks_sells_when_configured(self, gate, monkeypatch):
        monkeypatch.setattr(settings, "RISK_HALT_BLOCKS_SELLS", True)
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": -800, "unrealized_pl": 0,
                     "cash": 50_000, "buying_power": 100_000},
            positions=[],
        )
        decision = gate.evaluate("AAPL", "SELL", 0.0)
        assert decision.approved is False


# ── Drawdown ────────────────────────────────────────────────────────────────

class TestDrawdown:
    def test_drawdown_above_cap_blocks(self, gate, monkeypatch):
        # Seed peak equity well above current
        from app.services.utils import record_account_snapshot
        record_account_snapshot(equity=200_000)

        _patch_broker(
            monkeypatch,
            account={"equity": 150_000, "day_pl": 0, "unrealized_pl": 0,
                     "cash": 50_000, "buying_power": 150_000},
            positions=[],
        )
        # 200k → 150k = 25% drawdown > 15% cap
        decision = gate.evaluate("AAPL", "BUY", 1000.0)
        assert decision.approved is False
        assert any("drawdown" in b for b in decision.breaches)

    def test_within_drawdown_cap_approved(self, gate, monkeypatch):
        from app.services.utils import record_account_snapshot
        record_account_snapshot(equity=100_000)

        _patch_broker(
            monkeypatch,
            account={"equity": 95_000, "day_pl": 0, "unrealized_pl": 0,
                     "cash": 50_000, "buying_power": 95_000},
            positions=[],
        )
        # 5% drawdown < 15% cap
        decision = gate.evaluate("AAPL", "BUY", 1000.0)
        assert decision.approved is True


# ── Audit persistence ────────────────────────────────────────────────────────

class TestRiskAudit:
    def test_blocked_event_persisted(self, gate, monkeypatch):
        from app.services.utils import get_risk_events
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": -600, "unrealized_pl": 0,
                     "cash": 50_000, "buying_power": 100_000},
            positions=[],
        )
        gate.evaluate("RISKSYM", "BUY", 1000.0)
        events = get_risk_events(limit=5, symbol="RISKSYM")
        assert len(events) >= 1
        assert events[0]["approved"] is False
        assert "daily_loss" in events[0]["reason"]

    def test_approved_event_persisted(self, gate, monkeypatch):
        from app.services.utils import get_risk_events
        _patch_broker(
            monkeypatch,
            account={"equity": 100_000, "day_pl": 0, "unrealized_pl": 0,
                     "cash": 50_000, "buying_power": 100_000},
            positions=[],
        )
        gate.evaluate("HAPPYSYM", "BUY", 500.0)
        events = get_risk_events(limit=5, symbol="HAPPYSYM")
        assert len(events) >= 1
        assert events[0]["approved"] is True


# ── Underlying symbol parsing ────────────────────────────────────────────────

class TestUnderlyingParser:
    def test_occ_with_prefix(self):
        from app.services.risk_gate import _underlying
        assert _underlying("O:AAPL250117C00185000") == "AAPL"

    def test_occ_without_prefix(self):
        from app.services.risk_gate import _underlying
        assert _underlying("AAPL250117C00185000") == "AAPL"

    def test_bare_ticker(self):
        from app.services.risk_gate import _underlying
        assert _underlying("MSFT") == "MSFT"

    def test_empty(self):
        from app.services.risk_gate import _underlying
        assert _underlying("") is None


# ── /trades/risk-status endpoint ─────────────────────────────────────────────

class TestRiskStatusEndpoint:
    def test_endpoint_returns_snapshot(self, client, mock_alpaca):
        # mock_alpaca's get_account_pnl is a MagicMock — give it a sane return.
        mock_alpaca.get_account_pnl.return_value = {
            "equity": 100_000, "day_pl": 100, "unrealized_pl": 50,
            "cash": 50_000, "buying_power": 100_000,
        }
        mock_alpaca.get_positions_pnl.return_value = []
        # The risk_gate imports the singleton at module import, so patch that
        # specific reference (not just app.routes.trades.alpaca_client).
        with patch("app.services.risk_gate.alpaca_client", mock_alpaca):
            resp = client.get("/trades/risk-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "risk" in body
        assert "limits" in body["risk"]
        assert "recent_events" in body


# ── Token telemetry in /trades/ai-status ─────────────────────────────────────

class TestTokenTelemetry:
    def test_ai_status_includes_token_stats(self, client, mock_alpaca):
        resp = client.get("/trades/ai-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "tokens" in body
        assert "total_prompt_tokens" in body["tokens"]
        assert "total_completion_tokens" in body["tokens"]
        assert "top_symbols" in body["tokens"]
