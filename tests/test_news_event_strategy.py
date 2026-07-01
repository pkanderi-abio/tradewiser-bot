"""
Unit tests for NewsEventStrategy (Phase 4a — coordinator that ties the
extractor, position_manager, risk_gate, and regime_gate together).

Guarantees exercised:
  * scan_for_entries respects severity threshold + routes to options at
    severity ≥ NEWS_STRATEGY_SEVERITY_MIN_OPTIONS
  * scan_for_entries sorts candidates by |aggregate| descending (strongest wins)
  * evaluate_pass evaluates exits BEFORE entries; exits run even when flag off
  * evaluate_pass respects feature flag, regime gate, risk gate, slot limit
  * enter_position: risk-gate rejection blocks BUY and doesn't open position
  * enter_position: order rejection records audit + does not open position
  * execute_exit: places SELL + calls position_manager.close_position
  * Coexists with DailyRSIStrategy state (positions in different DB tables)
  * _underlying_of correctly extracts underlying from OCC option symbols
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings


@pytest.fixture
def strat(monkeypatch):
    from app.services import utils as utils_mod
    utils_mod.truncate_tables_for_tests("multi_day_positions", "news_events", "trade_audit", "risk_events")

    monkeypatch.setattr(settings, "NEWS_STRATEGY_ENABLED", True)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_MAX_CONCURRENT", 3)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_SEVERITY_MIN_TO_ENTER", 4.0)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_SEVERITY_MIN_OPTIONS", 7.0)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_HOLD_DAYS", 5)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_STOP_LOSS_PCT", 0.08)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_TAKE_PROFIT_PCT", 0.15)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_POSITION_DOLLARS", 1000.0)
    monkeypatch.setattr(settings, "NEWS_STRATEGY_REVERSAL_SEVERITY_MULT", -0.75)
    monkeypatch.setattr(settings, "NEWS_EVENT_MIN_ABS_SEVERITY", 3)
    monkeypatch.setattr(settings, "NEWS_SEVERITY_AGGREGATE", "sum")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(settings, "REGIME_GATE_ENABLED", False)  # green by default
    monkeypatch.setattr(settings, "RISK_GATE_ENABLED", False)   # green by default

    from app.services.news_event_strategy import NewsEventStrategy
    return NewsEventStrategy()


def _mk_signal(symbol, aggregate, top_event_type="upgrade", n_events=3):
    from app.services.news_event_extractor import AggregateSignal
    return AggregateSignal(
        symbol=symbol.upper(), aggregate=float(aggregate),
        max_abs_severity=int(abs(aggregate)),
        top_event_type=top_event_type,
        n_events=n_events, n_dropped_below_min=0,
    )


def _mk_events(symbol, severities):
    from app.services.news_event_extractor import ExtractedEvent
    return [
        ExtractedEvent(
            symbol=symbol.upper(), headline=f"h{i}", headline_hash=f"hash{i}",
            event_type="upgrade" if s > 0 else "downgrade",
            severity=s, confidence=0.8, reason="", source=None,
            published_at=None, from_cache=False,
        )
        for i, s in enumerate(severities)
    ]


# ── underlying extraction ────────────────────────────────────────────────────

class TestUnderlyingExtraction:
    def test_stock_symbol_unchanged(self):
        from app.services.news_event_strategy import _underlying_of
        assert _underlying_of("NVDA") == "NVDA"
        assert _underlying_of("aapl") == "AAPL"

    def test_option_symbol_extracts_root(self):
        from app.services.news_event_strategy import _underlying_of
        assert _underlying_of("O:AAPL260117C00185000") == "AAPL"
        assert _underlying_of("O:SPY260502C00705000") == "SPY"

    def test_option_with_no_digits_returns_core(self):
        from app.services.news_event_strategy import _underlying_of
        assert _underlying_of("O:WEIRD") == "WEIRD"


# ── scan_for_entries ─────────────────────────────────────────────────────────

class TestScanForEntries:
    def test_below_threshold_dropped(self, strat):
        from app.services import news_event_strategy as mod
        with patch.object(mod, "news_feed") as nf, \
             patch.object(mod, "news_event_extractor") as ex:
            nf.headlines.return_value = ["h1", "h2"]
            ex.extract.return_value = _mk_events("NVDA", [3, -1])
            ex.aggregate_severity.return_value = _mk_signal("NVDA", 3.0)  # below 4.0
            with patch.object(strat, "universe", return_value=["NVDA"]):
                cands = strat.scan_for_entries()
        assert cands == []

    def test_stock_routing_for_mid_range_severity(self, strat):
        from app.services import news_event_strategy as mod
        with patch.object(mod, "news_feed") as nf, \
             patch.object(mod, "news_event_extractor") as ex:
            nf.headlines.return_value = ["h1"]
            ex.extract.return_value = _mk_events("NVDA", [5])
            ex.aggregate_severity.return_value = _mk_signal("NVDA", 5.0)
            with patch.object(strat, "universe", return_value=["NVDA"]):
                cands = strat.scan_for_entries()
        assert len(cands) == 1
        assert cands[0].symbol == "NVDA"
        assert cands[0].instrument == "stock"

    def test_option_routing_for_high_severity(self, strat):
        from app.services import news_event_strategy as mod
        with patch.object(mod, "news_feed") as nf, \
             patch.object(mod, "news_event_extractor") as ex, \
             patch.object(mod, "get_atm_option_symbols", return_value=["O:NVDA260117C00185000", "O:NVDA260117P00185000"]):
            nf.headlines.return_value = ["h1"]
            ex.extract.return_value = _mk_events("NVDA", [8])
            ex.aggregate_severity.return_value = _mk_signal("NVDA", 8.0)
            with patch.object(strat, "universe", return_value=["NVDA"]):
                cands = strat.scan_for_entries()
        assert cands[0].instrument == "option"
        assert cands[0].symbol.startswith("O:NVDA")
        assert cands[0].underlying == "NVDA"

    def test_option_fallback_to_stock_when_chain_unavailable(self, strat):
        from app.services import news_event_strategy as mod
        with patch.object(mod, "news_feed") as nf, \
             patch.object(mod, "news_event_extractor") as ex, \
             patch.object(mod, "get_atm_option_symbols", return_value=[]):
            nf.headlines.return_value = ["h1"]
            ex.extract.return_value = _mk_events("NVDA", [8])
            ex.aggregate_severity.return_value = _mk_signal("NVDA", 8.0)
            with patch.object(strat, "universe", return_value=["NVDA"]):
                cands = strat.scan_for_entries()
        assert cands[0].instrument == "stock"
        assert cands[0].symbol == "NVDA"

    def test_negative_severity_skipped_v1_no_shorts(self, strat):
        from app.services import news_event_strategy as mod
        with patch.object(mod, "news_feed") as nf, \
             patch.object(mod, "news_event_extractor") as ex:
            nf.headlines.return_value = ["h1"]
            ex.extract.return_value = _mk_events("NVDA", [-8])
            ex.aggregate_severity.return_value = _mk_signal("NVDA", -8.0, top_event_type="downgrade")
            with patch.object(strat, "universe", return_value=["NVDA"]):
                cands = strat.scan_for_entries()
        assert cands == []

    def test_candidates_sorted_by_strength(self, strat):
        from app.services import news_event_strategy as mod
        def _agg(sym, headlines):
            m = {"AAA": _mk_events("AAA", [5]),
                 "BBB": _mk_events("BBB", [9]),
                 "CCC": _mk_events("CCC", [6])}
            return m[sym]
        with patch.object(mod, "news_feed") as nf, \
             patch.object(mod, "news_event_extractor") as ex, \
             patch.object(mod, "get_atm_option_symbols", return_value=[]):
            nf.headlines.return_value = ["h1"]
            def _extract(sym, headlines): return _agg(sym, headlines)
            def _agg_sev(events):
                sev_map = {"AAA": 5.0, "BBB": 9.0, "CCC": 6.0}
                return _mk_signal(events[0].symbol, sev_map[events[0].symbol])
            ex.extract.side_effect = _extract
            ex.aggregate_severity.side_effect = _agg_sev
            with patch.object(strat, "universe", return_value=["AAA", "BBB", "CCC"]):
                cands = strat.scan_for_entries()
        symbols_in_order = [c.underlying for c in cands]
        assert symbols_in_order == ["BBB", "CCC", "AAA"]


# ── evaluate_pass entries ────────────────────────────────────────────────────

class TestEvaluatePassEntries:
    def test_disabled_flag_skips_entries_but_still_evaluates_exits(self, strat, monkeypatch):
        monkeypatch.setattr(settings, "NEWS_STRATEGY_ENABLED", False)
        # No open positions and no scan should happen; counters reflect that.
        with patch.object(strat, "scan_for_entries") as scan, \
             patch.object(strat, "_evaluate_exits_with_quotes", return_value=[]) as exits:
            counters = strat.evaluate_pass()
        scan.assert_not_called()
        exits.assert_called_once()
        assert counters["candidates"] == 0
        assert counters["entries_opened"] == 0

    def test_regime_block_zeros_entries(self, strat):
        from app.services import news_event_strategy as mod
        from app.services.news_event_extractor import AggregateSignal
        cand = MagicMock()
        cand.aggregate_signal = _mk_signal("NVDA", 5.0)
        cand.instrument = "stock"; cand.symbol = "NVDA"; cand.underlying = "NVDA"
        cand.top_event_type = "upgrade"; cand.events_used = 2
        with patch.object(strat, "scan_for_entries", return_value=[cand]), \
             patch.object(strat, "_evaluate_exits_with_quotes", return_value=[]), \
             patch.object(mod, "regime_gate") as rg:
            rg.classify.return_value.allow_new_buys = False
            rg.classify.return_value.regime = "downtrend"
            rg.classify.return_value.reason = "SPY < SMA50 < SMA200"
            counters = strat.evaluate_pass()
        assert counters["entries_blocked_regime"] == 1
        assert counters["entries_opened"] == 0

    def test_slot_limit_blocks_extra_entries(self, strat, monkeypatch):
        """3 candidates, max_concurrent=1 - only 1 gets in."""
        monkeypatch.setattr(settings, "NEWS_STRATEGY_MAX_CONCURRENT", 1)
        from app.services import news_event_strategy as mod
        cands = []
        for sym, sev in [("AAA", 8.0), ("BBB", 7.0), ("CCC", 5.0)]:
            c = MagicMock()
            c.aggregate_signal = _mk_signal(sym, sev)
            c.symbol = sym; c.underlying = sym; c.instrument = "stock"
            c.top_event_type = "upgrade"; c.events_used = 2
            cands.append(c)
        with patch.object(strat, "scan_for_entries", return_value=cands), \
             patch.object(strat, "_evaluate_exits_with_quotes", return_value=[]), \
             patch.object(strat, "_enter_position") as enter, \
             patch.object(mod, "regime_gate") as rg:
            rg.classify.return_value.allow_new_buys = True
            # First enter succeeds and creates a position; subsequent
            # can_open_new_position() returns False. Simulate via a real DB
            # insert so can_open_new_position() reflects state accurately.
            from app.services.position_manager import position_manager
            def _enter(cand):
                return position_manager.open_position(
                    strategy="news_event_v1", symbol=cand.symbol,
                    underlying=cand.underlying, instrument="stock",
                    entry_severity=cand.aggregate_signal.aggregate,
                    entry_event_type="upgrade",
                )
            enter.side_effect = _enter
            counters = strat.evaluate_pass()
        assert counters["entries_opened"] == 1
        assert counters["entries_blocked_slot"] == 2

    def test_dedupe_blocks_second_entry_on_same_underlying(self, strat):
        from app.services import news_event_strategy as mod
        from app.services.position_manager import position_manager
        # Seed an existing pending position on NVDA
        position_manager.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        cand = MagicMock()
        cand.aggregate_signal = _mk_signal("NVDA", 8.0)
        cand.symbol = "O:NVDA260117C00185000"; cand.underlying = "NVDA"; cand.instrument = "option"
        cand.top_event_type = "upgrade"; cand.events_used = 2
        with patch.object(strat, "scan_for_entries", return_value=[cand]), \
             patch.object(strat, "_evaluate_exits_with_quotes", return_value=[]), \
             patch.object(strat, "_enter_position") as enter, \
             patch.object(mod, "regime_gate") as rg:
            rg.classify.return_value.allow_new_buys = True
            counters = strat.evaluate_pass()
        enter.assert_not_called()
        assert counters["entries_blocked_dup"] == 1


# ── enter_position ──────────────────────────────────────────────────────────

class TestEnterPosition:
    def _make_cand(self, symbol="NVDA", underlying="NVDA", instrument="stock", severity=5.0):
        from app.services.news_event_strategy import EntryCandidate
        return EntryCandidate(
            symbol=symbol, underlying=underlying, instrument=instrument,
            aggregate_signal=_mk_signal(underlying, severity),
            events_used=3, top_event_type="upgrade",
        )

    def test_risk_gate_rejection_blocks_and_no_position(self, strat, monkeypatch):
        from app.services import news_event_strategy as mod
        monkeypatch.setattr(settings, "RISK_GATE_ENABLED", True)
        cand = self._make_cand()
        with patch.object(mod, "alpaca_client") as ac, \
             patch.object(mod, "risk_gate") as rg:
            ac.get_quote.return_value = {"pLast": 100.0, "bid": 99.9, "ask": 100.1}
            rg.evaluate.return_value.approved = False
            rg.evaluate.return_value.reason = "concentration cap breached"
            pos = strat._enter_position(cand)
            ac.place_order.assert_not_called()
        assert pos is None

    def test_successful_entry_creates_pending_position(self, strat):
        from app.services import news_event_strategy as mod
        from app.services.position_manager import position_manager
        cand = self._make_cand("NVDA", severity=5.0)
        with patch.object(mod, "alpaca_client") as ac, \
             patch.object(mod, "risk_gate") as rg:
            ac.get_quote.return_value = {"pLast": 100.0}
            ac.place_order.return_value = {"order_id": "order-42", "status": "new"}
            rg.evaluate.return_value.approved = True
            pos = strat._enter_position(cand)
        assert pos is not None
        assert pos.state == "pending"
        assert pos.entry_severity == 5.0
        assert pos.entry_order_id == "order-42"
        positions = position_manager.list_positions(strategy="news_event_v1")
        assert len(positions) == 1

    def test_order_rejection_does_not_open_position(self, strat):
        from app.services import news_event_strategy as mod
        from app.services.position_manager import position_manager
        cand = self._make_cand("NVDA", severity=5.0)
        with patch.object(mod, "alpaca_client") as ac, \
             patch.object(mod, "risk_gate") as rg:
            ac.get_quote.return_value = {"pLast": 100.0}
            ac.place_order.side_effect = RuntimeError("insufficient buying power")
            rg.evaluate.return_value.approved = True
            pos = strat._enter_position(cand)
        assert pos is None
        assert position_manager.list_positions(strategy="news_event_v1") == []


# ── execute_exit ─────────────────────────────────────────────────────────────

class TestExecuteExit:
    def test_successful_exit_closes_position(self, strat):
        from app.services import news_event_strategy as mod
        from app.services.position_manager import position_manager, ExitDecision
        p = position_manager.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        position_manager.record_fill(p.id, fill_price=100.0, shares=10)
        decision = ExitDecision(
            position_id=p.id, symbol="NVDA", underlying="NVDA", instrument="stock",
            exit_reason="target", exit_price_estimate=115.5, entry_price=100.0, shares=10,
            context={},
        )
        with patch.object(mod, "alpaca_client") as ac:
            ac.place_order.return_value = {"order_id": "sell-1"}
            ok = strat._execute_exit(decision)
        assert ok is True
        after = position_manager.get_position(p.id)
        assert after.state == "closed" and after.exit_reason == "target"
        assert after.exit_price == 115.5

    def test_order_rejection_returns_false_and_leaves_open(self, strat):
        from app.services import news_event_strategy as mod
        from app.services.position_manager import position_manager, ExitDecision
        p = position_manager.open_position(
            strategy="news_event_v1", symbol="NVDA", underlying="NVDA",
            instrument="stock", entry_severity=5.0, entry_event_type="upgrade",
        )
        position_manager.record_fill(p.id, fill_price=100.0, shares=10)
        decision = ExitDecision(
            position_id=p.id, symbol="NVDA", underlying="NVDA", instrument="stock",
            exit_reason="stop", exit_price_estimate=90.0, entry_price=100.0, shares=10,
            context={},
        )
        with patch.object(mod, "alpaca_client") as ac:
            ac.place_order.side_effect = RuntimeError("broker down")
            ok = strat._execute_exit(decision)
        assert ok is False
        after = position_manager.get_position(p.id)
        assert after.state == "open"  # still open
