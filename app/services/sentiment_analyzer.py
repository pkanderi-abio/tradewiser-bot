"""
Sentiment Analysis Module — Analyze market sentiment from news, social media, and market data.

Features:
  - News sentiment analysis (financial news, earnings, analyst reports)
  - Social sentiment tracking (sentiment scores over time)
  - Market sentiment indicators (VIX, put/call ratios, breadth)
  - AI-powered sentiment interpretation
"""

import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.logger import logger
from app.services.llm_service import LLMService


class SentimentAnalyzer:
    """Analyze market and news sentiment using LLM and indicators."""

    def __init__(self):
        self.llm = LLMService()
        self._sentiment_cache: Dict[str, Tuple[float, dict]] = {}
        self._cache_ttl = 3600

    def analyze_news_sentiment(self, symbol: str, headlines: List[str]) -> dict:
        """
        Analyze sentiment of news headlines about a symbol.

        Returns:
            {
                "overall_sentiment": "bullish|neutral|bearish",
                "score": -1.0 to 1.0,
                "headline_sentiments": [
                    {"headline": "...", "sentiment": "bullish", "score": 0.85}
                ],
                "key_themes": ["earnings", "product launch", "regulatory"],
                "urgency": "high|medium|low",
            }
        """
        if not headlines:
            return {
                "overall_sentiment": "neutral",
                "score": 0.0,
                "headline_sentiments": [],
                "key_themes": [],
                "urgency": "low",
            }

        prompt = f"""Analyze the sentiment of these {symbol} news headlines:

{json.dumps(headlines[:10])}

Return JSON with:
- overall_sentiment: "bullish", "neutral", or "bearish"
- score: -1.0 (very bearish) to 1.0 (very bullish)
- headline_sentiments: list of {{"headline": str, "sentiment": str, "score": float}}
- key_themes: list of key topics (earnings, guidance, product, etc.)
- urgency: "high", "medium", or "low"

Focus on: materiality, financial impact, timeline urgency."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a financial sentiment analyst. Analyze {symbol} news with a neutral, fact-based perspective.",
                temperature=0.3,  # Lower temp for consistency
                cache_key=f"news_sentiment_{symbol}_{len(headlines)}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"News sentiment analysis failed for {symbol}: {e}")
            return {
                "overall_sentiment": "neutral",
                "score": 0.0,
                "headline_sentiments": [],
                "key_themes": [],
                "urgency": "low",
                "error": str(e),
            }

    def analyze_market_sentiment(
        self,
        symbol: str,
        vix: Optional[float] = None,
        market_breadth: Optional[dict] = None,
        sector_performance: Optional[dict] = None,
    ) -> dict:
        """
        Analyze broader market sentiment indicators.

        Args:
            symbol: Stock symbol
            vix: VIX index level (fear gauge)
            market_breadth: {"advances": int, "declines": int, "unchanged": int}
            sector_performance: {"sector": float} (pct change)

        Returns:
            {
                "market_sentiment": "bullish|neutral|bearish",
                "vix_signal": high_fear|normal|low_fear,
                "breadth_signal": strong_up|weak|mixed|strong_down,
                "sector_relative_strength": float,
                "overall_score": -1.0 to 1.0,
                "interpretation": str,
            }
        """
        context = {
            "vix": vix or "N/A",
            "market_breadth": market_breadth or {},
            "sector_performance": sector_performance or {},
        }

        prompt = f"""Analyze the market sentiment based on these indicators for {symbol}:

VIX Level: {context['vix']}
Market Breadth: {json.dumps(context['market_breadth'])}
Sector Performance: {json.dumps(context['sector_performance'])}

Return JSON with:
- market_sentiment: "bullish", "neutral", or "bearish"
- vix_signal: "high_fear" (VIX > 20), "normal", or "low_fear" (VIX < 15)
- breadth_signal: "strong_up", "weak", "mixed", or "strong_down"
- sector_relative_strength: -1.0 to 1.0 (sector vs market)
- overall_score: -1.0 (very bearish) to 1.0 (very bullish)
- interpretation: brief explanation

Guidelines:
- High VIX + weak breadth = bearish confluence
- Low VIX + strong breadth = bullish confluence
- Mixed signals warrant neutral bias"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt="You are a technical market analyst. Interpret market indicators with precision.",
                temperature=0.3,
                cache_key=f"market_sentiment_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Market sentiment analysis failed: {e}")
            return {
                "market_sentiment": "neutral",
                "vix_signal": "normal",
                "breadth_signal": "mixed",
                "sector_relative_strength": 0.0,
                "overall_score": 0.0,
                "interpretation": "Unable to analyze",
                "error": str(e),
            }

    def analyze_social_sentiment(
        self,
        symbol: str,
        mentions: int,
        sentiment_scores: List[float],
        trending: bool = False,
    ) -> dict:
        """
        Analyze social media sentiment for a symbol.

        Args:
            symbol: Stock symbol
            mentions: Number of mentions in period
            sentiment_scores: List of individual sentiment scores
            trending: Whether symbol is trending

        Returns:
            {
                "social_sentiment": "bullish|neutral|bearish",
                "avg_score": float,
                "momentum": "increasing|stable|decreasing",
                "viral_potential": "high|medium|low",
                "noise_level": "high|medium|low",
                "recommendation": str,
            }
        """
        if not sentiment_scores:
            return {
                "social_sentiment": "neutral",
                "avg_score": 0.0,
                "momentum": "stable",
                "viral_potential": "low",
                "noise_level": "low",
                "recommendation": "Insufficient social data",
            }

        avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
        trend = "increasing" if mentions > 100 else "decreasing" if mentions < 10 else "stable"

        prompt = f"""Analyze social media sentiment for {symbol}:

Total Mentions: {mentions}
Average Sentiment Score: {avg_sentiment:.2f} (-1 to 1)
Recent Trend: {trend}
Trending: {trending}
Individual Scores (sample): {sentiment_scores[:20]}

Return JSON with:
- social_sentiment: "bullish", "neutral", or "bearish" (based on avg score)
- avg_score: {avg_sentiment:.2f}
- momentum: "increasing", "stable", or "decreasing"
- viral_potential: "high", "medium", or "low"
- noise_level: "high" (many mentions but low signal), "medium", or "low"
- recommendation: brief trading implication

Guidelines:
- Avg score > 0.3 = bullish tendency
- -0.3 < avg score < 0.3 = neutral
- Avg score < -0.3 = bearish tendency
- High mentions + trending = potential volatility
- Consider noise from retail vs institutional"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt="You are a social media sentiment analyst for trading. Interpret retail sentiment carefully.",
                temperature=0.3,
                cache_key=f"social_sentiment_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Social sentiment analysis failed for {symbol}: {e}")
            return {
                "social_sentiment": "neutral",
                "avg_score": avg_sentiment,
                "momentum": "stable",
                "viral_potential": "low",
                "noise_level": "high",
                "recommendation": "Social data inconclusive",
                "error": str(e),
            }

    def combine_sentiments(
        self,
        symbol: str,
        news_sentiment: dict,
        market_sentiment: dict,
        social_sentiment: dict,
    ) -> dict:
        """
        Combine multiple sentiment sources into unified recommendation.

        Returns:
            {
                "unified_sentiment": "strong_bullish|bullish|neutral|bearish|strong_bearish",
                "confidence": 0.0-1.0,
                "signal_confluence": "strong|moderate|weak",
                "buy_signal": bool,
                "sell_signal": bool,
                "hold_signal": bool,
                "reasoning": str,
                "sources": {
                    "news": float,
                    "market": float,
                    "social": float,
                    "weighted_average": float,
                }
            }
        """
        news_score = news_sentiment.get("score", 0.0)
        market_score = market_sentiment.get("overall_score", 0.0)
        social_score = social_sentiment.get("avg_score", 0.0)

        # Weighted average (news and market get higher weight)
        weighted_avg = (news_score * 0.4 + market_score * 0.4 + social_score * 0.2)

        # Signal confluence check
        sentiments = [
            news_sentiment.get("overall_sentiment", "neutral"),
            market_sentiment.get("market_sentiment", "neutral"),
            social_sentiment.get("social_sentiment", "neutral"),
        ]
        bullish_count = sentiments.count("bullish")
        bearish_count = sentiments.count("bearish")
        confluence = "strong" if bullish_count >= 2 or bearish_count >= 2 else "weak"

        # Determine unified sentiment
        if weighted_avg > 0.5:
            unified = "strong_bullish" if weighted_avg > 0.7 else "bullish"
        elif weighted_avg < -0.5:
            unified = "strong_bearish" if weighted_avg < -0.7 else "bearish"
        else:
            unified = "neutral"

        prompt = f"""Synthesize trading sentiment for {symbol}:

News Sentiment: {news_sentiment.get('overall_sentiment', 'neutral')} (score: {news_score:.2f})
Market Sentiment: {market_sentiment.get('market_sentiment', 'neutral')} (score: {market_score:.2f})
Social Sentiment: {social_sentiment.get('social_sentiment', 'neutral')} (score: {social_score:.2f})

Weighted Average: {weighted_avg:.2f}
Signal Confluence: {confluence}

Return JSON with:
- unified_sentiment: "strong_bullish", "bullish", "neutral", "bearish", or "strong_bearish"
- confidence: 0.0-1.0 (how confident in the signal)
- buy_signal: bool
- sell_signal: bool
- hold_signal: bool
- reasoning: concise explanation of the combined signal

Guidelines:
- Strong confluence of bullish = HIGH confidence buy
- Mixed signals = neutral/hold
- Strong confluence of bearish = HIGH confidence sell"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt="You are a senior trading advisor. Synthesize multiple sentiment sources into actionable signals.",
                temperature=0.2,
                cache_key=f"combined_sentiment_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            result["sources"] = {
                "news": news_score,
                "market": market_score,
                "social": social_score,
                "weighted_average": weighted_avg,
            }
            return result
        except Exception as e:
            logger.error(f"Combined sentiment analysis failed for {symbol}: {e}")
            return {
                "unified_sentiment": "neutral",
                "confidence": 0.0,
                "signal_confluence": "weak",
                "buy_signal": False,
                "sell_signal": False,
                "hold_signal": True,
                "reasoning": "Unable to combine sentiments",
                "sources": {
                    "news": news_score,
                    "market": market_score,
                    "social": social_score,
                    "weighted_average": weighted_avg,
                },
                "error": str(e),
            }
