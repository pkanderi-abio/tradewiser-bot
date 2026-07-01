"""NewsEventStrategy calibration - is our severity score predictive?

Reads closed positions from multi_day_positions and produces:
  * overall hit rate + realized P&L
  * per-event-type breakdown (which event categories are our edge?)
  * per-severity-bucket breakdown (does higher severity => higher P&L?)
  * notes flagging small sample sizes so downstream consumers know what to trust

The report is a diagnostic, not an auto-tuner. We do NOT automatically adjust
thresholds. This is deliberate: threshold changes require human review, and
LLM-scored severity has enough label drift that mechanical retuning would
overfit to the last few weeks. Change thresholds by editing settings, not by
a Bayesian update from this report.

Public API:
    news_strategy_calibration.compute(window_days=30) -> CalibrationReport
    news_strategy_calibration.snapshot(window_days=30) -> dict  # jsonable

Consumed by:
    /trades/news-strategy endpoint (Phase 6)
    status.ps1 dashboard (Phase 6)
    ad-hoc CLI reports
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.core.logger import logger
from app.services.utils import _connect, _lock


# ── Data types ─────────────────────────────────────────────────────────────

@dataclass
class Bucket:
    """One breakdown row - grouping by event_type or severity bucket."""
    label: str
    n: int
    wins: int
    losses: int
    hit_rate: float
    total_pnl: float
    mean_pnl: float
    mean_pct_return: float
    avg_severity: float


@dataclass
class CalibrationReport:
    window_days: int
    n_positions_closed: int
    n_positions_open: int
    hit_rate: float
    total_realized_pnl: float
    mean_pct_return: float
    total_events: int              # count of news_events rows scored in window
    error_rate_events: float       # extractor failure rate over the window
    by_event_type: List[Bucket] = field(default_factory=list)
    by_severity_bucket: List[Bucket] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_days": self.window_days,
            "n_positions_closed": self.n_positions_closed,
            "n_positions_open": self.n_positions_open,
            "hit_rate": self.hit_rate,
            "total_realized_pnl": self.total_realized_pnl,
            "mean_pct_return": self.mean_pct_return,
            "total_events": self.total_events,
            "error_rate_events": self.error_rate_events,
            "by_event_type": [asdict(b) for b in self.by_event_type],
            "by_severity_bucket": [asdict(b) for b in self.by_severity_bucket],
            "notes": list(self.notes),
        }


# ── Helpers ────────────────────────────────────────────────────────────────

def _severity_bucket(sev: float) -> str:
    """Group severities into readable ranges. Buckets biased toward the values
    the strategy actually enters at (>=4)."""
    a = abs(float(sev))
    if a < 2:
        return "0-1"
    if a < 4:
        return "2-3"
    if a < 6:
        return "4-5"
    if a < 8:
        return "6-7"
    return "8+"


def _bucketize(rows: List[Dict[str, Any]], key_fn) -> List[Bucket]:
    """Group rows by key_fn(row) and produce ordered Bucket rows."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        k = key_fn(r)
        groups.setdefault(k, []).append(r)
    buckets: List[Bucket] = []
    for label, items in groups.items():
        n = len(items)
        wins = sum(1 for it in items if (it.get("realized_pnl") or 0) > 0)
        losses = sum(1 for it in items if (it.get("realized_pnl") or 0) < 0)
        total = sum((it.get("realized_pnl") or 0.0) for it in items)
        entry_prices = [(it.get("entry_price") or 0.0) for it in items]
        # Percent return per position = realized_pnl / (entry_price * shares)
        pct_returns = []
        for it in items:
            entry = it.get("entry_price") or 0.0
            shares = it.get("shares") or 0
            notional = entry * shares
            if notional > 0:
                pct_returns.append((it.get("realized_pnl") or 0.0) / notional)
        mean_pct = sum(pct_returns) / len(pct_returns) if pct_returns else 0.0
        avg_sev = (sum(float(it.get("entry_severity") or 0.0) for it in items) / n) if n else 0.0
        buckets.append(Bucket(
            label=label, n=n, wins=wins, losses=losses,
            hit_rate=round(wins / n, 3) if n else 0.0,
            total_pnl=round(total, 2),
            mean_pnl=round(total / n, 2) if n else 0.0,
            mean_pct_return=round(mean_pct, 4),
            avg_severity=round(avg_sev, 2),
        ))
    return buckets


# ── Service ────────────────────────────────────────────────────────────────

class NewsStrategyCalibration:
    STRATEGY = "news_event_v1"

    def compute(self, window_days: int = 30, strategy: Optional[str] = None) -> CalibrationReport:
        strategy = strategy or self.STRATEGY
        with _lock:
            conn = _connect()
            closed_rows = conn.execute(
                """
                SELECT symbol, underlying, instrument, entry_price, shares,
                       entry_severity, entry_event_type, exit_price, exit_reason,
                       realized_pnl, entry_filled_at, exit_filled_at
                FROM multi_day_positions
                WHERE state = 'closed'
                  AND strategy = ?
                  AND exit_filled_at >= datetime('now', ?)
                """,
                (strategy, f"-{int(window_days)} days"),
            ).fetchall()
            open_row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM multi_day_positions
                WHERE state IN ('pending', 'open') AND strategy = ?
                """,
                (strategy,),
            ).fetchone()
            evt_rows = conn.execute(
                """
                SELECT outcome, COUNT(*) AS n FROM news_events
                WHERE timestamp >= datetime('now', ?)
                GROUP BY outcome
                """,
                (f"-{int(window_days)} days",),
            ).fetchall()

        closed = [dict(r) for r in closed_rows]
        n_open = int(open_row["n"]) if open_row else 0

        # Aggregate top-level metrics
        n_closed = len(closed)
        wins = sum(1 for c in closed if (c.get("realized_pnl") or 0) > 0)
        total_pnl = sum((c.get("realized_pnl") or 0.0) for c in closed)
        pct_returns = []
        for c in closed:
            entry = c.get("entry_price") or 0.0
            shares = c.get("shares") or 0
            notional = entry * shares
            if notional > 0:
                pct_returns.append((c.get("realized_pnl") or 0.0) / notional)
        mean_pct = sum(pct_returns) / len(pct_returns) if pct_returns else 0.0

        # Event rollups
        outcome_counts = {r["outcome"]: r["n"] for r in evt_rows}
        total_events = sum(outcome_counts.values())
        fail_bucket = (outcome_counts.get("llm_error", 0)
                       + outcome_counts.get("timeout", 0)
                       + outcome_counts.get("schema_error", 0)
                       + outcome_counts.get("circuit_open", 0))
        error_rate = round(fail_bucket / total_events, 3) if total_events else 0.0

        # Bucket by event_type and severity
        by_event_type = _bucketize(closed, key_fn=lambda r: r.get("entry_event_type") or "other")
        # Sort event-type buckets by mean_pct_return desc so best categories float up
        by_event_type.sort(key=lambda b: -b.mean_pct_return)

        by_sev = _bucketize(closed, key_fn=lambda r: _severity_bucket(r.get("entry_severity") or 0))
        # Order severity buckets from highest to lowest (mirrors human intuition
        # "does higher severity => better return?")
        SEV_ORDER = ["8+", "6-7", "4-5", "2-3", "0-1"]
        by_sev.sort(key=lambda b: SEV_ORDER.index(b.label) if b.label in SEV_ORDER else 99)

        notes: List[str] = []
        if n_closed < 20:
            notes.append(f"only {n_closed} closed positions in window — hit-rate/P&L are noisy; need 20+ for direction, 30+ for tightening thresholds")
        if error_rate > 0.10:
            notes.append(f"extractor error rate {error_rate:.1%} exceeds 10% — investigate provider health before trusting the signal")
        if n_closed >= 20 and wins / n_closed < 0.35:
            notes.append(f"hit rate {wins/n_closed:.1%} is below 35% — signal may be inverted or thresholds too permissive")
        if total_events == 0:
            notes.append("no news_events in window — extractor may be disabled or not receiving headlines")

        report = CalibrationReport(
            window_days=window_days,
            n_positions_closed=n_closed,
            n_positions_open=n_open,
            hit_rate=round(wins / n_closed, 3) if n_closed else 0.0,
            total_realized_pnl=round(total_pnl, 2),
            mean_pct_return=round(mean_pct, 4),
            total_events=total_events,
            error_rate_events=error_rate,
            by_event_type=by_event_type,
            by_severity_bucket=by_sev,
            notes=notes,
        )
        logger.info(
            f"[news_calibration] window={window_days}d closed={n_closed} open={n_open} "
            f"hit_rate={report.hit_rate:.1%} pnl={total_pnl:+.2f} events={total_events} err={error_rate:.1%}"
        )
        return report

    def snapshot(self, window_days: int = 30) -> Dict[str, Any]:
        return self.compute(window_days=window_days).to_dict()


news_strategy_calibration = NewsStrategyCalibration()
