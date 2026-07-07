"""
News severity gate — decides whether recent headline severity supports new BUYs.

Ported from the news-event severity experiment (with fixes for guards, filtering, etc.).

Uses NewsAnalyzer to score headlines (LLM) and aggregate severity.

- High positive aggregate = supportive for BUY
- Strongly negative aggregate = block BUY (fail-closed for new longs)
- Fails open on errors (don't let news scoring kill the whole system)

Exposed via snapshot for /trades/ai-status or dedicated status.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional

from app.core.config import settings
from app.core.logger import logger
from app.services.news_event_extractor import news_event_extractor
from app.services.news_feed import news_feed


@dataclass
class NewsSeverityDecision:
    aggregate: float
    allow_new_buys: bool
    reason: str
    scored_count: int
    top_events: List[dict]

    def to_dict(self) -> dict:
        return asdict(self)


class NewsSeverityGate:
    """Lightweight view over the production news_event_extractor for simple severity decisions.

    Preferred over the older NewsAnalyzer path for consistency with NewsEventStrategy.
    """

    def evaluate(self, symbol: str) -> NewsSeverityDecision:
        """Return severity decision for a symbol using the hardened extractor."""
        if not getattr(settings, "NEWS_SEVERITY_ENABLED", True):
            return NewsSeverityDecision(
                aggregate=0.0,
                allow_new_buys=True,
                reason="news severity gate disabled",
                scored_count=0,
                top_events=[],
            )

        try:
            headlines = news_feed.headlines(symbol)
            if not headlines:
                return NewsSeverityDecision(
                    aggregate=0.0,
                    allow_new_buys=True,
                    reason="no recent headlines",
                    scored_count=0,
                    top_events=[],
                )

            # Use the production extractor (full reliability, caching to news_events table, etc.)
            events = news_event_extractor.extract(symbol, headlines)
            agg_signal = news_event_extractor.aggregate_severity(events)

            if agg_signal is None:
                aggregate = 0.0
                n_events = 0
                top_event = None
            else:
                aggregate = agg_signal.aggregate
                n_events = agg_signal.n_events
                top_event = agg_signal.top_event_type

            min_agg = getattr(settings, "NEWS_SEVERITY_MIN_AGGREGATE", 4.0)
            allow = True
            reason = f"aggregate={aggregate:.1f} (lookback {getattr(settings, 'NEWS_SEVERITY_LOOKBACK_DAYS', 3)}d, events={n_events})"

            if aggregate < -min_agg:
                allow = False
                reason = f"aggregate={aggregate:.1f} < -{min_agg} — negative news severity blocks BUY"
                logger.info(f"[NEWS_SEVERITY] {symbol} blocked: {reason}")
            elif aggregate >= min_agg:
                logger.info(f"[NEWS_SEVERITY] {symbol} positive severity {aggregate:.1f} — supportive for BUY")

            # Build lightweight top events for status/debug (best effort)
            top_events = []
            if events:
                sorted_events = sorted(events, key=lambda e: -abs(e.severity))[:3]
                top_events = [
                    {
                        "event_type": e.event_type,
                        "severity": e.severity,
                        "headline": e.headline[:120] + ("..." if len(e.headline) > 120 else ""),
                    }
                    for e in sorted_events
                ]

            return NewsSeverityDecision(
                aggregate=round(aggregate, 1),
                allow_new_buys=allow,
                reason=reason,
                scored_count=n_events,
                top_events=top_events,
            )

        except Exception as e:
            logger.warning(f"[news_severity] evaluate failed for {symbol} — fail-open: {e}")
            return NewsSeverityDecision(
                aggregate=0.0,
                allow_new_buys=True,
                reason=f"scoring error ({e}) — fail-open",
                scored_count=0,
                top_events=[],
            )

    def snapshot(self) -> dict:
        """For status endpoints."""
        base = {
            "enabled": getattr(settings, "NEWS_SEVERITY_ENABLED", True),
            "min_aggregate": getattr(settings, "NEWS_SEVERITY_MIN_AGGREGATE", 4.0),
            "lookback_days": getattr(settings, "NEWS_SEVERITY_LOOKBACK_DAYS", 3),
            "aggregate_mode": getattr(settings, "NEWS_SEVERITY_AGGREGATE", "sum"),
        }
        try:
            # Merge extractor snapshot for richer status (circuit, cache stats, etc.)
            extractor_snap = news_event_extractor.snapshot()
            return {**base, "extractor": extractor_snap}
        except Exception:
            return base


news_severity_gate = NewsSeverityGate()
