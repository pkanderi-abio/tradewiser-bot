"""
Enhanced AI Advisor — Integrates multiple AI capabilities for superior trading decisions.

Combines:
  - LLM-powered trade recommendations
  - Sentiment analysis (news, market, social)
  - News analysis and event detection
  - Market intelligence and pattern recognition
  - Technical analysis with AI interpretation
  - Multi-factor confidence scoring

Priority Chain:
  1. Sentiment analysis (news, market, social)
  2. News events and catalysts
  3. Market regime and patterns
  4. LLM trade decision
  5. Risk assessment
  6. Confidence scoring
"""

import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import time

import yfinance as yf

from app.core.config import settings
from app.core.logger import logger
from app.services.llm_service import LLMService
from app.services.sentiment_analyzer import SentimentAnalyzer
from app.services.news_analyzer import NewsAnalyzer
from app.services.market_intelligence import MarketIntelligence
from app.services.watchlist_manager import EXPERT_PICKS


class EnhancedAIAdvisor:
    """Enhanced AI advisor combining multiple AI models and analysis techniques."""

    def __init__(self):
        self.llm = LLMService()
        self.sentiment = SentimentAnalyzer()
        self.news = NewsAnalyzer()
        self.market_intel = MarketIntelligence()
        
        self._decision_cache: Dict[str, Tuple[float, dict]] = {}
        self._cache_ttl = settings.AI_DECISION_CACHE_TTL

        logger.info(
            f"Enhanced AI Advisor initialized | "
            f"LLM Provider: {self.llm.get_provider()} | "
            f"Model: {self.llm.get_model()}"
        )

    def get_ai_capabilities(self) -> dict:
        """Get information about available AI capabilities."""
        return {
            "llm": self.llm.get_capabilities(),
            "sentiment_analysis": settings.AI_SENTIMENT_ENABLED,
            "news_analysis": settings.AI_NEWS_ENABLED,
            "market_intelligence": settings.AI_MARKET_INTELLIGENCE_ENABLED,
            "min_confidence": settings.AI_MIN_CONFIDENCE,
            "cache_ttl": self._cache_ttl,
        }

    def make_trading_decision(
        self,
        symbol: str,
        price: float,
        technical_data: Optional[dict] = None,
        market_data: Optional[dict] = None,
        news_headlines: Optional[List[str]] = None,
        social_data: Optional[dict] = None,
    ) -> dict:
        """
        Make comprehensive trading decision integrating all AI models.

        Args:
            symbol: Stock symbol
            price: Current price
            technical_data: {"rsi": float, "macd": float, "sma20": float, etc.}
            market_data: {"vix": float, "breadth": dict, "sector": str, etc.}
            news_headlines: Recent headlines
            social_data: {"mentions": int, "sentiment_scores": List[float]}

        Returns:
            {
                "action": "BUY|SELL|HOLD",
                "confidence": 0.0-1.0,
                "price_target": float,
                "stop_loss": float,
                "reasoning": str,
                "signals": {
                    "sentiment": {...},
                    "technical": {...},
                    "news": {...},
                    "market_regime": {...},
                },
                "risks": [...],
                "opportunities": [...],
                "recommended_position_size": 0.5-2.0,
                "time_horizon": "intraday|short_term|medium_term|long_term",
            }
        """
        # Check cache
        cache_key = f"trading_decision_{symbol}_{price}"
        if cache_key in self._decision_cache:
            ts, decision = self._decision_cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                logger.debug(f"Cache hit for trading decision: {symbol}")
                return decision

        try:
            # 1. Sentiment Analysis
            sentiment_signals = {}
            if settings.AI_SENTIMENT_ENABLED and news_headlines:
                logger.info(f"Analyzing sentiment for {symbol}")
                news_sentiment = self.sentiment.analyze_news_sentiment(symbol, news_headlines)
                market_sentiment = self.sentiment.analyze_market_sentiment(
                    symbol,
                    vix=market_data.get("vix") if market_data else None,
                    market_breadth=market_data.get("breadth") if market_data else None,
                )
                social_sentiment = self.sentiment.analyze_social_sentiment(
                    symbol,
                    mentions=social_data.get("mentions", 0) if social_data else 0,
                    sentiment_scores=social_data.get("sentiment_scores", []) if social_data else [],
                )
                sentiment_signals = self.sentiment.combine_sentiments(
                    symbol, news_sentiment, market_sentiment, social_sentiment
                )

            # 2. News & Event Analysis
            news_signals = {}
            if settings.AI_NEWS_ENABLED and news_headlines:
                logger.info(f"Analyzing news events for {symbol}")
                events = self.news.detect_events(symbol, news_headlines)
                news_summary = self.news.generate_news_summary(symbol, news_headlines)
                news_signals = {
                    "events": events,
                    "summary": news_summary,
                }

            # 3. Technical & Market Intelligence
            technical_signals = {}
            market_signals = {}
            if settings.AI_MARKET_INTELLIGENCE_ENABLED:
                if technical_data:
                    logger.info(f"Analyzing technical patterns for {symbol}")
                    patterns = self.market_intel.identify_technical_patterns(
                        symbol,
                        price_data=technical_data.get("prices", [price]),
                        volume_data=technical_data.get("volume"),
                        timeframe="1D",
                    )
                    technical_signals["patterns"] = patterns

                if market_data:
                    logger.info(f"Assessing market regime for {symbol}")
                    regime = self.market_intel.assess_market_regime(symbol, market_data)
                    market_signals["regime"] = regime

            # 4. LLM Trade Decision
            llm_decision = self._get_llm_recommendation(
                symbol,
                price,
                sentiment_signals,
                news_signals,
                technical_signals,
                market_signals,
            )

            # 5. Combine all signals
            final_decision = self._synthesize_decision(
                symbol,
                price,
                sentiment_signals,
                news_signals,
                technical_signals,
                market_signals,
                llm_decision,
            )

            # Cache decision
            self._decision_cache[cache_key] = (time.time(), final_decision)

            logger.info(
                f"Trading decision for {symbol}: {final_decision['action']} "
                f"(confidence: {final_decision['confidence']:.1%})"
            )

            return final_decision

        except Exception as e:
            logger.error(f"Trading decision error for {symbol}: {e}")
            return self._default_hold_decision(symbol, price, str(e))

    def _get_llm_recommendation(
        self,
        symbol: str,
        price: float,
        sentiment_signals: dict,
        news_signals: dict,
        technical_signals: dict,
        market_signals: dict,
    ) -> dict:
        """Get LLM recommendation based on all signal inputs."""
        prompt = f"""Make a trading recommendation for {symbol} at ${price:.2f}:

Sentiment Signals:
{json.dumps(sentiment_signals, indent=2)[:500]}

News Signals:
{json.dumps(news_signals, indent=2)[:500]}

Technical Signals:
{json.dumps(technical_signals, indent=2)[:500]}

Market Signals:
{json.dumps(market_signals, indent=2)[:500]}

Return JSON with:
- action: "BUY", "SELL", or "HOLD"
- confidence: 0-1 confidence in the action
- price_target: estimated price target (% upside if BUY, % downside if SELL)
- stop_loss: suggested stop-loss price or percentage
- time_horizon: "intraday", "short_term" (1-5 days), "medium_term" (1-4 weeks), or "long_term" (1+ months)
- reasoning: 2-3 sentence summary
- risks: list of 2-3 key risks
- opportunities: list of 2-3 upside opportunities

Weighting:
- Strong consensus (2+ bullish signals) = BUY bias
- Strong consensus (2+ bearish signals) = SELL bias
- Mixed signals = HOLD bias
- News catalysts > sentiment > technical"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a senior trading advisor for {symbol}. Make precise, actionable recommendations.",
                temperature=0.4,
                cache_key=f"llm_rec_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"LLM recommendation failed: {e}")
            return {
                "action": "HOLD",
                "confidence": 0.5,
                "price_target": price * 1.05,
                "stop_loss": price * 0.95,
                "time_horizon": "medium_term",
                "reasoning": "Unable to get LLM recommendation",
                "risks": ["LLM service unavailable"],
                "opportunities": [],
            }

    def _synthesize_decision(
        self,
        symbol: str,
        price: float,
        sentiment_signals: dict,
        news_signals: dict,
        technical_signals: dict,
        market_signals: dict,
        llm_decision: dict,
    ) -> dict:
        """Synthesize all signals into final trading decision."""
        # Calculate overall confidence
        signal_count = 0
        bullish_signals = 0
        bearish_signals = 0

        # Sentiment contribution
        if sentiment_signals.get("unified_sentiment"):
            signal_count += 1
            if "bullish" in sentiment_signals["unified_sentiment"]:
                bullish_signals += 1
            elif "bearish" in sentiment_signals["unified_sentiment"]:
                bearish_signals += 1

        # News contribution
        if news_signals.get("events"):
            signal_count += 1
            for event in news_signals["events"]:
                if event.get("direction") == "bullish":
                    bullish_signals += 0.5
                elif event.get("direction") == "bearish":
                    bearish_signals += 0.5

        # Technical contribution
        if technical_signals.get("patterns"):
            signal_count += 1
            for pattern in technical_signals["patterns"]:
                if pattern.get("trade_setup") == "long":
                    bullish_signals += 0.5
                elif pattern.get("trade_setup") == "short":
                    bearish_signals += 0.5

        # Market regime
        if market_signals.get("regime"):
            regime = market_signals["regime"]["regime"]
            if "uptrend" in regime:
                bullish_signals += 0.5
                signal_count += 0.5
            elif "downtrend" in regime:
                bearish_signals += 0.5
                signal_count += 0.5

        # LLM decision weight
        if llm_decision.get("action") == "BUY":
            bullish_signals += 1
        elif llm_decision.get("action") == "SELL":
            bearish_signals += 1
        signal_count += 1

        # Calculate confidence
        net_signal = bullish_signals - bearish_signals
        raw_confidence = abs(net_signal) / max(signal_count, 1)
        final_confidence = min(raw_confidence, 1.0) * (llm_decision.get("confidence", 0.5))

        # Determine action
        if net_signal > 0 and final_confidence >= settings.AI_MIN_CONFIDENCE:
            action = "BUY"
        elif net_signal < 0 and final_confidence >= settings.AI_MIN_CONFIDENCE:
            action = "SELL"
        else:
            action = "HOLD"

        # Calculate position sizing
        position_size = 1.0 + (final_confidence - settings.AI_MIN_CONFIDENCE) * 2
        position_size = min(position_size, 2.0)  # Max 2x

        # Build final decision
        decision = {
            "symbol": symbol,
            "price": price,
            "action": action,
            "confidence": final_confidence,
            "price_target": llm_decision.get("price_target", price),
            "stop_loss": llm_decision.get("stop_loss", price * 0.95),
            "time_horizon": llm_decision.get("time_horizon", "medium_term"),
            "reasoning": llm_decision.get("reasoning", "Mixed signals"),
            "recommended_position_size": position_size,
            "signals": {
                "sentiment": sentiment_signals,
                "technical": technical_signals,
                "news": news_signals,
                "market_regime": market_signals,
                "llm": llm_decision,
            },
            "risks": llm_decision.get("risks", []),
            "opportunities": llm_decision.get("opportunities", []),
            "decision_timestamp": datetime.now().isoformat(),
        }

        return decision

    def _default_hold_decision(self, symbol: str, price: float, error: str) -> dict:
        """Return safe HOLD decision on error."""
        return {
            "symbol": symbol,
            "price": price,
            "action": "HOLD",
            "confidence": 0.0,
            "price_target": price,
            "stop_loss": price * 0.95,
            "time_horizon": "medium_term",
            "reasoning": f"Error in decision making: {error}",
            "recommended_position_size": 1.0,
            "signals": {},
            "risks": [error],
            "opportunities": [],
            "decision_timestamp": datetime.now().isoformat(),
        }
