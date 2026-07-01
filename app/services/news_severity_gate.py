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
from app.services.news_analyzer import NewsAnalyzer
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
    def __init__(self):
        self._analyzer = NewsAnalyzer()

    def evaluate(self, symbol: str) -> NewsSeverityDecision:
        """Return severity decision for a symbol."""
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
            scored = self._analyzer.score_headline_severities(symbol, headlines)
            aggregate = self._analyzer.aggregate_severity(scored)

            min_agg = getattr(settings, "NEWS_SEVERITY_MIN_AGGREGATE", 4.0)
            allow = True
            reason = f"aggregate={aggregate:.1f} (lookback {getattr(settings, 'NEWS_SEVERITY_LOOKBACK_DAYS', 3)}d)"

            if aggregate < -min_agg:
                allow = False
                reason = f"aggregate={aggregate:.1f} < -{min_agg} — negative news severity blocks BUY"
                logger.info(f"[NEWS_SEVERITY] {symbol} blocked: {reason}")
            elif aggregate >= min_agg:
                logger.info(f"[NEWS_SEVERITY] {symbol} positive severity {aggregate:.1f} — supportive for BUY")

            # Limit top events for output
            top = sorted(scored, key=lambda x: -abs(x.get("severity", 0)))[:3] if scored else []

            return NewsSeverityDecision(
                aggregate=round(aggregate, 1),
                allow_new_buys=allow,
                reason=reason,
                scored_count=len(scored),
                top_events=top,
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
        return {
            "enabled": getattr(settings, "NEWS_SEVERITY_ENABLED", True),
            "min_aggregate": getattr(settings, "NEWS_SEVERITY_MIN_AGGREGATE", 4.0),
            "lookback_days": getattr(settings, "NEWS_SEVERITY_LOOKBACK_DAYS", 3),
            "max_to_score": getattr(settings, "NEWS_SEVERITY_MAX_TO_SCORE", 15),
            "aggregate_mode": getattr(settings, "NEWS_SEVERITY_AGGREGATE", "sum"),
        }


news_severity_gate = NewsSeverityGate()
