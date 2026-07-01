"""Tests for news_strategy_calibration - checks the report over synthetic
closed positions in multi_day_positions."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.config import settings


@pytest.fixture
def seed_positions(monkeypatch):
    """Insert a small set of closed positions with known severities and P&L."""
    from app.services import utils as utils_mod
    utils_mod.truncate_tables_for_tests("multi_day_positions")

    from app.services.position_manager import position_manager
    seeds = [
        # (underlying, severity, event_type, entry_price, exit_price, shares)
        ("NVDA", 8.0, "earnings_beat", 100.0, 115.0, 10),
        ("NVDA", 7.0, "upgrade",       100.0, 105.0, 10),
        ("TSLA", 6.0, "product_launch",100.0,  98.0, 10),  # loser
        ("TSLA", 5.0, "partnership",   100.0, 108.0, 10),
        ("META", 4.0, "macro",         100.0,  95.0, 10),  # loser
        ("META", 4.5, "upgrade",       100.0, 103.0, 10),
    ]
    for i, (sym, sev, ev, entry, exitp, shares) in enumerate(seeds):
        p = position_manager.open_position(
            strategy="news_event_v1", symbol=sym, underlying=sym,
            instrument="stock", entry_severity=sev, entry_event_type=ev,
        )
        position_manager.record_fill(p.id, fill_price=entry, shares=shares)
        position_manager.close_position(p.id, exit_price=exitp, exit_reason="time")
    return seeds


class TestReport:
    def test_report_counts_all_closed_positions(self, seed_positions):
        from app.services.news_strategy_calibration import news_strategy_calibration
        report = news_strategy_calibration.compute(window_days=30)
        assert report.n_positions_closed == 6
        # 4 winners of 6 -> 2/3 hit rate
        assert report.hit_rate == pytest.approx(4 / 6, abs=0.01)

    def test_by_event_type_groups_correctly(self, seed_positions):
        from app.services.news_strategy_calibration import news_strategy_calibration
        report = news_strategy_calibration.compute(window_days=30)
        labels = {b.label for b in report.by_event_type}
        assert {"earnings_beat", "upgrade", "product_launch", "partnership", "macro"} <= labels
        upgrade = next(b for b in report.by_event_type if b.label == "upgrade")
        assert upgrade.n == 2 and upgrade.wins == 2

    def test_severity_buckets_higher_severity_first(self, seed_positions):
        from app.services.news_strategy_calibration import news_strategy_calibration
        report = news_strategy_calibration.compute(window_days=30)
        # First bucket has highest avg severity
        assert report.by_severity_bucket[0].avg_severity >= report.by_severity_bucket[-1].avg_severity

    def test_severity_bucket_labels(self, seed_positions):
        from app.services.news_strategy_calibration import news_strategy_calibration, _severity_bucket
        assert _severity_bucket(0.4) == "0-1"
        assert _severity_bucket(2.5) == "2-3"
        assert _severity_bucket(5) == "4-5"
        assert _severity_bucket(7) == "6-7"
        assert _severity_bucket(10) == "8+"

    def test_low_sample_notes_added(self, seed_positions):
        from app.services.news_strategy_calibration import news_strategy_calibration
        report = news_strategy_calibration.compute(window_days=30)
        # 6 closed positions - well below the 20-threshold; note should be present.
        assert any("noisy" in n or "20+" in n or "30+" in n for n in report.notes)

    def test_empty_returns_zero_stats(self):
        from app.services import utils as utils_mod
        utils_mod.truncate_tables_for_tests("multi_day_positions")
        from app.services.news_strategy_calibration import news_strategy_calibration
        report = news_strategy_calibration.compute(window_days=30)
        assert report.n_positions_closed == 0
        assert report.hit_rate == 0.0
        assert report.total_realized_pnl == 0.0
