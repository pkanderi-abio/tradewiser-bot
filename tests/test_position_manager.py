"""
Unit tests for the multi-day PositionManager (Phase 3 state machine).

Guarantees exercised:
  * State transitions: (none) -> pending -> open -> closed
  * Stop / target / reversal / time exits fire in the documented priority order
  * evaluate_exits returns no decisions for positions in state != 'open'
  * Reversal exit fires only when new severity magnitude beats threshold with opposite sign
  * Concurrent-slot limit enforced by can_open_new_position()
  * Realized P&L is computed from entry_price + shares on close_position()
  * list_positions filters by state/symbol/underlying/strategy
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings


@pytest.fixture
def pm(monkeypatch):
    from app.services import utils as utils_mod
    utils_mod.truncate_tables_for_tests("multi_day_positions")

    # Predictable defaults for tests
    monkeypatch.setattr(settings, "NEWS_STRATEGY_HOLD_DAYS", 5)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_STOP_LOSS_PCT", 0.08)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_TAKE_PROFIT_PCT", 0.15)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_REVERSAL_SEVERITY_MULT", -0.75)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_MAX_CONCURRENT", 3)

    from app.services.position_manager import PositionManager
    return PositionManager()


def _open_and_fill(pm, symbol="NVDA", underlying="NVDA", instrument="stock",
                   fill_price=100.0, shares=10, severity=6.0, event_type="upgrade"):
    pos = pm.open_position(
        strategy="news_event_v1",
        symbol=symbol, underlying=underlying, instrument=instrument,
        entry_severity=severity, entry_event_type=event_type,
        entry_reason="test", entry_order_id="order-1",
    )
    filled = pm.record_fill(pos.id, fill_price=fill_price, shares=shares, broker_order_id="order-1")
    return filled


# ── State machine ────────────────────────────────────────────────────────────

class TestStateTransitions:
    def test_open_creates_pending(self, pm):
        pos = pm.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        assert pos.state == "pending"
        assert pos.id is not None
        assert pos.entry_price is None and pos.shares is None

    def test_record_fill_transitions_to_open(self, pm):
        pos = pm.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        filled = pm.record_fill(pos.id, fill_price=100.0, shares=10)
        assert filled.state == "open"
        assert filled.entry_price == 100.0 and filled.shares == 10
        # stop = 100 * (1 - 0.08) = 92; target = 100 * (1 + 0.15) = 115
        assert filled.stop_level == pytest.approx(92.0)
        assert filled.target_level == pytest.approx(115.0)

    def test_close_transitions_to_closed_with_pnl(self, pm):
        pos = _open_and_fill(pm, fill_price=100.0, shares=10)
        closed = pm.close_position(pos.id, exit_price=110.0, exit_reason="target")
        assert closed.state == "closed"
        assert closed.exit_price == 110.0
        assert closed.realized_pnl == pytest.approx(100.0)  # (110-100)*10

    def test_record_fill_ignored_on_already_open(self, pm):
        pos = _open_and_fill(pm, fill_price=100.0, shares=10)
        # Second call should not double-fill
        again = pm.record_fill(pos.id, fill_price=200.0, shares=99)
        assert again.entry_price == 100.0 and again.shares == 10

    def test_close_idempotent_on_already_closed(self, pm):
        pos = _open_and_fill(pm, fill_price=100.0, shares=10)
        pm.close_position(pos.id, exit_price=110.0, exit_reason="target")
        again = pm.close_position(pos.id, exit_price=50.0, exit_reason="stop")
        assert again.state == "closed" and again.exit_price == 110.0


# ── Exit decisions ──────────────────────────────────────────────────────────

class TestEvaluateExits:
    def test_stop_fires_when_price_below_stop_level(self, pm):
        pos = _open_and_fill(pm, fill_price=100.0, shares=10)  # stop=92, target=115
        decisions = pm.evaluate_exits({"NVDA": 91.0})
        assert len(decisions) == 1
        assert decisions[0].exit_reason == "stop"
        assert decisions[0].position_id == pos.id

    def test_target_fires_when_price_above_target_level(self, pm):
        _open_and_fill(pm, fill_price=100.0, shares=10)
        decisions = pm.evaluate_exits({"NVDA": 116.0})
        assert len(decisions) == 1
        assert decisions[0].exit_reason == "target"

    def test_stop_priority_over_target_intrabar(self, pm):
        """If both stop and target boundaries could fire on the same input,
        stop wins (conservative). We simulate this by passing a price at/below
        stop -- target is not checked because stop returns first."""
        _open_and_fill(pm, fill_price=100.0, shares=10)
        decisions = pm.evaluate_exits({"NVDA": 92.0})
        assert decisions[0].exit_reason == "stop"

    def test_reversal_fires_when_new_severity_flips_and_beats_threshold(self, pm):
        _open_and_fill(pm, fill_price=100.0, shares=10, severity=6.0)  # thresh = 6*0.75 = 4.5
        decisions = pm.evaluate_exits(
            {"NVDA": 100.0},
            aggregate_signals={"NVDA": -5.0},
        )
        assert len(decisions) == 1
        assert decisions[0].exit_reason == "reversal"

    def test_reversal_does_not_fire_when_new_severity_below_threshold(self, pm):
        _open_and_fill(pm, fill_price=100.0, shares=10, severity=6.0)  # thresh = 4.5
        decisions = pm.evaluate_exits(
            {"NVDA": 100.0},
            aggregate_signals={"NVDA": -4.0},  # magnitude below threshold
        )
        assert decisions == []

    def test_reversal_does_not_fire_when_same_sign(self, pm):
        _open_and_fill(pm, fill_price=100.0, shares=10, severity=6.0)
        decisions = pm.evaluate_exits(
            {"NVDA": 100.0},
            aggregate_signals={"NVDA": 8.0},  # same sign as entry - no reversal
        )
        assert decisions == []

    def test_reversal_works_for_short_entry_signals(self, pm):
        """Entered on negative severity - reversal exits on positive severity above threshold."""
        _open_and_fill(pm, fill_price=100.0, shares=10, severity=-6.0)  # thresh = 4.5
        decisions = pm.evaluate_exits(
            {"NVDA": 100.0},
            aggregate_signals={"NVDA": 5.0},
        )
        assert len(decisions) == 1 and decisions[0].exit_reason == "reversal"

    def test_time_exit_fires_when_hold_until_passed(self, pm):
        pos = _open_and_fill(pm, fill_price=100.0, shares=10)
        as_of = datetime.now(timezone.utc) + timedelta(days=6)  # past hold_until (5d)
        decisions = pm.evaluate_exits({"NVDA": 100.0}, as_of=as_of)
        assert len(decisions) == 1 and decisions[0].exit_reason == "time"

    def test_no_decision_when_no_quote(self, pm):
        _open_and_fill(pm, fill_price=100.0, shares=10)
        # Passing empty quotes dict - should skip evaluation
        decisions = pm.evaluate_exits({})
        assert decisions == []

    def test_closed_positions_are_not_evaluated(self, pm):
        pos = _open_and_fill(pm, fill_price=100.0, shares=10)
        pm.close_position(pos.id, exit_price=105.0, exit_reason="manual")
        decisions = pm.evaluate_exits({"NVDA": 50.0})  # would trigger stop if open
        assert decisions == []

    def test_pending_positions_are_not_evaluated(self, pm):
        pm.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        decisions = pm.evaluate_exits({"NVDA": 50.0})
        assert decisions == []


# ── Concurrent slot limit ────────────────────────────────────────────────────

class TestConcurrentSlots:
    def test_can_open_when_under_limit(self, pm, monkeypatch):
        monkeypatch.setattr(settings, "NEWS_STRATEGY_MAX_CONCURRENT", 3)
        assert pm.can_open_new_position() is True
        _open_and_fill(pm, symbol="NVDA", underlying="NVDA")
        _open_and_fill(pm, symbol="TSLA", underlying="TSLA")
        assert pm.can_open_new_position() is True

    def test_cannot_open_at_limit(self, pm, monkeypatch):
        monkeypatch.setattr(settings, "NEWS_STRATEGY_MAX_CONCURRENT", 2)
        _open_and_fill(pm, symbol="NVDA", underlying="NVDA")
        _open_and_fill(pm, symbol="TSLA", underlying="TSLA")
        assert pm.can_open_new_position() is False

    def test_closed_positions_do_not_count_toward_limit(self, pm, monkeypatch):
        monkeypatch.setattr(settings, "NEWS_STRATEGY_MAX_CONCURRENT", 2)
        p1 = _open_and_fill(pm, symbol="NVDA", underlying="NVDA")
        _open_and_fill(pm, symbol="TSLA", underlying="TSLA")
        assert pm.can_open_new_position() is False
        pm.close_position(p1.id, exit_price=110.0, exit_reason="target")
        assert pm.can_open_new_position() is True

    def test_pending_positions_count_toward_limit(self, pm, monkeypatch):
        """Prevents a race where a pending BUY is not yet filled - the slot
        should still be reserved."""
        monkeypatch.setattr(settings, "NEWS_STRATEGY_MAX_CONCURRENT", 1)
        pm.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        # No fill yet, but slot is used
        assert pm.can_open_new_position() is False


# ── list_positions filters ──────────────────────────────────────────────────

class TestListFilters:
    def test_filter_by_state(self, pm):
        p1 = _open_and_fill(pm, symbol="NVDA", underlying="NVDA")
        pm.open_position(strategy="news_event_v1", symbol="TSLA", underlying="TSLA",
                         instrument="stock", entry_severity=5, entry_event_type="upgrade")
        open_only = pm.list_positions(state="open")
        pending_only = pm.list_positions(state="pending")
        assert {p.symbol for p in open_only} == {"NVDA"}
        assert {p.symbol for p in pending_only} == {"TSLA"}

    def test_filter_by_symbol(self, pm):
        _open_and_fill(pm, symbol="NVDA", underlying="NVDA")
        _open_and_fill(pm, symbol="TSLA", underlying="TSLA")
        got = pm.list_positions(symbol="tsla")
        assert len(got) == 1 and got[0].symbol == "TSLA"

    def test_filter_by_underlying_for_options(self, pm):
        """A call option like O:NVDA... should be findable via underlying=NVDA."""
        _open_and_fill(pm, symbol="O:NVDA260117C00185000", underlying="NVDA", instrument="option")
        got = pm.list_positions(underlying="NVDA")
        assert len(got) == 1 and got[0].instrument == "option"


# ── Snapshot ──────────────────────────────────────────────────────────────

class TestSnapshot:
    def test_snapshot_reports_state_counts(self, pm):
        _open_and_fill(pm, symbol="NVDA", underlying="NVDA")
        pm.open_position(strategy="news_event_v1", symbol="TSLA", underlying="TSLA",
                         instrument="stock", entry_severity=5, entry_event_type="upgrade")
        snap = pm.snapshot()
        assert snap["by_state"].get("open") == 1
        assert snap["by_state"].get("pending") == 1

    def test_snapshot_computes_hit_rate(self, pm):
        p1 = _open_and_fill(pm, symbol="NVDA", underlying="NVDA")
        p2 = _open_and_fill(pm, symbol="TSLA", underlying="TSLA")
        pm.close_position(p1.id, exit_price=110.0, exit_reason="target")
        pm.close_position(p2.id, exit_price=90.0, exit_reason="stop")
        snap = pm.snapshot()
        assert snap["trailing_30d"]["settled"] == 2
        assert snap["trailing_30d"]["wins"] == 1
        assert snap["trailing_30d"]["losses"] == 1
        assert snap["trailing_30d"]["hit_rate"] == 0.5
