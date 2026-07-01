"""
News Analysis Module — Comprehensive news gathering and analysis for trading decisions.

Features:
  - News aggregation from multiple sources
  - Event detection (earnings, FDA approvals, acquisitions, etc.)
  - News timeline analysis
  - Impact assessment on trading strategy
  - News-based risk alerts
"""

import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.logger import logger
from app.services.llm_service import LLMService
from app.services.ai_guardrails import sanitize_headlines
from app.services.utils import record_headline_score, get_headline_scores

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple


class NewsAnalyzer:
    """Gather, analyze, and interpret news for trading."""

    def __init__(self):
        self.llm = LLMService()
        self._news_cache: Dict[str, Tuple[float, List[str]]] = {}
        self._event_cache: Dict[str, Tuple[float, dict]] = {}
        self._cache_ttl = 3600

    def detect_events(
        self,
        symbol: str,
        headlines: List[str],
        news_summaries: Optional[List[str]] = None,
    ) -> dict:
        """
        Detect trading-relevant events from news.

        Returns:
            {
                "events": [
                    {
                        "type": "earnings",
                        "headline": str,
                        "impact": "high|medium|low",
                        "direction": "bullish|bearish|neutral",
                        "action_items": [...],
                    }
                ],
                "critical_events": [...],
                "timeline": {
                    "upcoming": [...],
                    "recent": [...],
                },
                "risk_level": "critical|high|medium|low|none",
            }
        """
        if not headlines:
            return {
                "events": [],
                "critical_events": [],
                "timeline": {"upcoming": [], "recent": []},
                "risk_level": "none",
            }

        prompt = f"""Detect trading events in {symbol} news:

Headlines:
{json.dumps(headlines[:15])}

Identify events like:
- Earnings announcements (scheduled or surprise)
- FDA approvals / regulatory actions
- Acquisitions / mergers / spin-offs
- CEO changes / leadership
- Product launches
- Litigation / investigations
- Dividend changes / share splits
- Analyst upgrades/downgrades

Return JSON with:
- events: list of {{"type": str, "headline": str, "impact": "high|medium|low", "direction": "bullish|bearish|neutral", "action_items": list}}
- critical_events: list of high-impact events only
- timeline: {{"upcoming": list, "recent": list}}
- risk_level: "critical", "high", "medium", "low", or "none"

Prioritize materiality to traders."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are an event analyst for {symbol}. Identify material trading events with precision.",
                temperature=0.2,
                cache_key=f"event_detection_{symbol}_{len(headlines)}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Event detection failed for {symbol}: {e}")
            return {
                "events": [],
                "critical_events": [],
                "timeline": {"upcoming": [], "recent": []},
                "risk_level": "low",
                "error": str(e),
            }

    def analyze_earnings(
        self,
        symbol: str,
        earnings_headlines: List[str],
        metrics: Optional[dict] = None,
    ) -> dict:
        """
        Deep dive analysis of earnings news and metrics.

        Args:
            symbol: Stock symbol
            earnings_headlines: News about earnings
            metrics: {
                "eps_beat": bool,
                "revenue_beat": bool,
                "guidance": "raised|maintained|lowered",
                "eps_value": float,
                "revenue_value": float,
            }

        Returns:
            {
                "earnings_surprise": "positive|neutral|negative",
                "guidance_implication": str,
                "beat_magnitude": float (-1 to 1),
                "forward_outlook": "positive|neutral|negative",
                "expected_volatility": "high|medium|low",
                "trading_signals": {
                    "momentum": float,
                    "support_resistance": {...},
                },
                "risks": [...],
                "opportunities": [...],
            }
        """
        context = {
            "earnings_headlines": earnings_headlines[:10],
            "metrics": metrics or {},
        }

        prompt = f"""Analyze earnings results and implications for {symbol}:

News Headlines:
{json.dumps(context['earnings_headlines'])}

Metrics:
{json.dumps(context['metrics'])}

Return JSON with:
- earnings_surprise: "positive" (beat on EPS/revenue), "neutral", or "negative" (miss)
- guidance_implication: interpretation of forward guidance
- beat_magnitude: -1.0 (big miss) to 1.0 (big beat)
- forward_outlook: "positive", "neutral", or "negative"
- expected_volatility: "high", "medium", or "low" for next 20 days
- trading_signals: {{"momentum": float, "reversion_likely": bool}}
- risks: list of downside risks mentioned
- opportunities: list of upside opportunities

Guidelines:
- Beat with raised guidance = very bullish
- Miss with lowered guidance = very bearish
- Beat with lowered guidance = mixed (quality of earnings question)
- Analyze tone and specificity of management comments"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are an earnings analyst for {symbol}. Provide precise earnings interpretation.",
                temperature=0.3,
                cache_key=f"earnings_analysis_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Earnings analysis failed for {symbol}: {e}")
            return {
                "earnings_surprise": "neutral",
                "guidance_implication": "Unable to determine",
                "beat_magnitude": 0.0,
                "forward_outlook": "neutral",
                "expected_volatility": "medium",
                "trading_signals": {"momentum": 0.0, "reversion_likely": False},
                "risks": [],
                "opportunities": [],
                "error": str(e),
            }

    def assess_acquisition_impact(
        self,
        symbol: str,
        acquirer_symbol: Optional[str],
        deal_terms: Optional[str],
        news: List[str],
    ) -> dict:
        """
        Assess impact of M&A activity on target stock.

        Returns:
            {
                "impact_type": "acquisition_target|acquirer|neutral",
                "deal_status": "announced|rumored|completed|failed",
                "fairness_assessment": "undervalued|fairly_valued|overvalued",
                "arb_opportunity": bool,
                "arb_spread": float,
                "timeline_to_close": str,
                "regulatory_risk": "high|medium|low",
                "trading_recommendation": str,
            }
        """
        prompt = f"""Analyze M&A impact on {symbol}:

Target/Acquirer: {symbol} vs {acquirer_symbol or 'unknown'}
Deal Terms: {deal_terms or 'unknown'}
News:
{json.dumps(news[:10])}

Return JSON with:
- impact_type: "acquisition_target", "acquirer", or "neutral"
- deal_status: "announced", "rumored", "completed", or "failed"
- fairness_assessment: "undervalued", "fairly_valued", or "overvalued"
- arb_opportunity: bool (is there spread opportunity?)
- arb_spread: estimated spread % if acquisition
- timeline_to_close: estimated or stated timeline
- regulatory_risk: "high", "medium", or "low" (antitrust, foreign, etc.)
- trading_recommendation: concise action for traders

Guidelines:
- Arb plays typically 2-5% spread
- Regulatory risk increases for large tech/financial deals
- Earlier announcement = higher execution risk"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are an M&A specialist. Analyze deal impact for {symbol}.",
                temperature=0.3,
                cache_key=f"ma_impact_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"M&A analysis failed for {symbol}: {e}")
            return {
                "impact_type": "neutral",
                "deal_status": "unknown",
                "fairness_assessment": "fairly_valued",
                "arb_opportunity": False,
                "arb_spread": 0.0,
                "timeline_to_close": "unknown",
                "regulatory_risk": "low",
                "trading_recommendation": "Monitor for updates",
                "error": str(e),
            }

    def identify_sector_themes(
        self,
        sector: str,
        sector_news: List[str],
        company_symbols: List[str],
    ) -> dict:
        """
        Identify major themes and trends in a sector.

        Returns:
            {
                "major_themes": [
                    {"theme": str, "prevalence": "widespread|notable|emerging", "impact": "bullish|neutral|bearish"}
                ],
                "winners": ["AAPL", "MSFT"],
                "losers": ["IBM"],
                "sector_direction": "strong_up|up|neutral|down|strong_down",
                "relative_strength": {"symbol": float},
                "rotation_signals": [...],
            }
        """
        prompt = f"""Analyze {sector} sector themes from news:

Sector News (sample):
{json.dumps(sector_news[:15])}

Companies: {', '.join(company_symbols[:10])}

Identify:
1. Major industry themes (AI, cloud, regulation, supply chain, etc.)
2. Winners and losers
3. Relative strength vs market
4. Rotation opportunities

Return JSON with:
- major_themes: list of {{"theme": str, "prevalence": "widespread|notable|emerging", "impact": "bullish|neutral|bearish"}}
- winners: list of outperforming symbols
- losers: list of underperforming symbols
- sector_direction: "strong_up", "up", "neutral", "down", or "strong_down"
- relative_strength: {{"symbol": float}} for each company
- rotation_signals: trading implication

Guidelines:
- Widespread themes affect entire sector
- Emerging themes often offer best opportunities
- Rotation from laggards to winners is key trade"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a sector strategist. Identify {sector} opportunities.",
                temperature=0.4,
                cache_key=f"sector_themes_{sector}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Sector theme analysis failed: {e}")
            return {
                "major_themes": [],
                "winners": [],
                "losers": [],
                "sector_direction": "neutral",
                "relative_strength": {},
                "rotation_signals": [],
                "error": str(e),
            }

    def generate_news_summary(
        self,
        symbol: str,
        headlines: List[str],
        days_lookback: int = 7,
    ) -> dict:
        """
        Generate concise executive summary of recent news.

        Returns:
            {
                "summary": str,
                "sentiment_trajectory": "improving|stable|deteriorating",
                "key_catalysts": [...],
                "watch_list": ["next_catalyst_1", "next_catalyst_2"],
                "conviction_level": 0.0-1.0,
            }
        """
        prompt = f"""Summarize recent news for {symbol} (last {days_lookback} days):

Headlines:
{json.dumps(headlines[:20])}

Return JSON with:
- summary: 2-3 sentence executive summary of key developments
- sentiment_trajectory: "improving", "stable", or "deteriorating"
- key_catalysts: list of most important near-term catalysts
- watch_list: what to watch for in next week
- conviction_level: 0-1 how confident in summary (1 = many clear signals)

Focus on trading relevance."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are an executive briefer for {symbol}. Be concise and actionable.",
                temperature=0.3,
                cache_key=f"news_summary_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"News summary generation failed for {symbol}: {e}")
            return {
                "summary": "Unable to generate summary",
                "sentiment_trajectory": "neutral",
                "key_catalysts": [],
                "watch_list": [],
                "conviction_level": 0.0,
                "error": str(e),
            }

    # ============================================================
    # News Severity Scoring (ported + hardened from news-event experiment)
    # ============================================================

    VALID_EVENT_TYPES = {
        "earnings_beat", "earnings_miss", "guidance_raise", "guidance_cut",
        "upgrade", "downgrade", "ma_rumor", "fda_approval", "fda_rejection",
        "lawsuit", "recall", "partnership", "product_launch", "macro", "other",
    }

    def _headline_hash(self, h: str) -> str:
        return hashlib.sha256(h.encode("utf-8")).hexdigest()[:32]

    def score_headline_severities(
        self,
        symbol: str,
        headlines: List[str],
    ) -> List[dict]:
        """
        Score recent headlines for event type and numeric severity.

        Returns list of:
            {
                "headline": str (sanitized),
                "event_type": str (from VALID_EVENT_TYPES),
                "severity": int -10..+10,
                "confidence": float 0..1,
                "reason": str,
            }

        Uses LLM (via LLMService) with sanitize, clamping, robust parse.
        Cached in-memory by headline hash for the analyzer instance.
        """
        if not headlines or not getattr(settings, "NEWS_SEVERITY_ENABLED", True):
            return []

        # Sanitize first (prompt injection defense)
        clean = sanitize_headlines(
            headlines,
            max_count=getattr(settings, "NEWS_SEVERITY_MAX_TO_SCORE", 15),
            max_chars=200,
        )
        if not clean:
            return []

        # Persistent DB cache for headline scores (port from experiment)
        db_cached = {}
        for row in get_headline_scores(symbol, limit=200):
            if row.get("headline_hash"):
                db_cached[row["headline_hash"]] = row

        prefilled = []
        remaining = []
        for h in clean:
            hh = self._headline_hash(h)
            if hh in db_cached:
                r = db_cached[hh]
                prefilled.append({
                    "headline": h,
                    "event_type": r.get("event_type", "other"),
                    "severity": int(r.get("severity", 0)),
                    "confidence": float(r.get("confidence", 0.5)),
                    "reason": r.get("reason", ""),
                })
            else:
                remaining.append(h)

        if not remaining:
            return prefilled

        clean = remaining

        # In-memory + LLM cache
        content_hash = self._headline_hash("||".join(clean))
        cache_key = f"severity_{symbol}_{content_hash}"
        if hasattr(self, "_sev_cache") and cache_key in getattr(self, "_sev_cache", {}):
            ts, res = self._sev_cache[cache_key]
            if time.time() - ts < 3600:
                return prefilled + res

        if not hasattr(self, "_sev_cache"):
            self._sev_cache: Dict[str, Tuple[float, List[dict]]] = {}

        # Sample prompt for severity scoring (ported + hardened from news-event experiment).
        # This is the exact template used for LLM calls to produce structured {event_type, severity} scores.
        # Feel free to evolve it; keep JSON-only output requirement for robust parsing.
        SEVERITY_SCORING_PROMPT_TEMPLATE = f"""You are a financial news analyst scoring headline impact for {symbol}.

For EACH headline below, assign:
- event_type: exactly one of {sorted(self.VALID_EVENT_TYPES)}
- severity: integer from -10 (strongly bearish) to +10 (strongly bullish)
- confidence: 0.0 to 1.0
- reason: 1 short sentence

Return ONLY a JSON array of objects (no other text, no ```):
[
  {{"headline": "original headline", "event_type": "...", "severity": 5, "confidence": 0.8, "reason": "..." }},
  ...
]

Headlines:
{json.dumps(clean, ensure_ascii=False)}

Clamp severity to [-10,10]. If unclear use event_type="other", severity=0."""

        prompt = SEVERITY_SCORING_PROMPT_TEMPLATE

        try:
            response = self.llm.query(
                prompt,
                system_prompt="Respond with strict JSON array only. Be objective and consistent.",
                temperature=0.1,
                max_tokens=1500,
                cache_key=cache_key,
            )
            data = self.llm.parse_json_response(response)
            if isinstance(data, dict):
                data = data.get("scores") or data.get("results") or list(data.values())[0] if data else []
            if not isinstance(data, list):
                data = []

            results: List[dict] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                hl = str(item.get("headline", ""))[:200]
                et = str(item.get("event_type", "other")).lower().replace(" ", "_")
                if et not in self.VALID_EVENT_TYPES:
                    et = "other"
                try:
                    sev = int(item.get("severity", 0))
                except Exception:
                    sev = 0
                sev = max(-10, min(10, sev))
                try:
                    conf = float(item.get("confidence", 0.5))
                except Exception:
                    conf = 0.5
                conf = max(0.0, min(1.0, conf))
                reason = str(item.get("reason", ""))[:120]
                results.append({
                    "headline": hl,
                    "event_type": et,
                    "severity": sev,
                    "confidence": round(conf, 2),
                    "reason": reason,
                })

                # Persist to DB for cross-run cache (idempotent)
                try:
                    record_headline_score(symbol, {
                        "headline_hash": self._headline_hash(hl),
                        "event_type": et,
                        "severity": sev,
                        "confidence": conf,
                        "reason": reason,
                        "model": getattr(self.llm, "_model", None),
                    })
                except Exception:
                    pass

            full_results = prefilled + results
            self._sev_cache[cache_key] = (time.time(), results)
            return full_results
        except Exception as e:
            logger.warning(f"[news_analyzer] severity scoring failed for {symbol}: {e}")
            return []

    def aggregate_severity(
        self,
        scored: List[dict],
        lookback_days: Optional[int] = None,
    ) -> float:
        """Aggregate using config (sum or mean)."""
        if not scored:
            return 0.0
        agg = getattr(settings, "NEWS_SEVERITY_AGGREGATE", "sum").lower()
        sevs = [float(s.get("severity", 0)) for s in scored]
        if agg in ("mean", "avg", "average"):
            return sum(sevs) / max(1, len(sevs))
        return sum(sevs)
