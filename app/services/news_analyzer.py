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
