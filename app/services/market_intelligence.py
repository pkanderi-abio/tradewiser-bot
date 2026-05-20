"""
Market Intelligence Service — Advanced market analysis and pattern recognition using LLM.

Features:
  - Technical pattern recognition (AI-powered)
  - Market regime identification
  - Volatility analysis and forecasting
  - Support/resistance identification
  - Divergence detection
  - Risk analysis and drawdown assessment
"""

import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from app.core.config import settings
from app.core.logger import logger
from app.services.llm_service import LLMService


class MarketIntelligence:
    """Advanced market analysis using LLM and technical indicators."""

    def __init__(self):
        self.llm = LLMService()
        self._pattern_cache: Dict[str, Tuple[float, dict]] = {}
        self._cache_ttl = 3600

    def identify_technical_patterns(
        self,
        symbol: str,
        price_data: List[float],
        volume_data: Optional[List[float]] = None,
        timeframe: str = "1D",
    ) -> dict:
        """
        Identify technical chart patterns using AI.

        Returns:
            {
                "patterns": [
                    {
                        "type": "double_bottom|head_shoulders|breakout|wedge|...",
                        "confidence": 0.0-1.0,
                        "price_target": float,
                        "timeframe": str,
                        "trade_setup": "long|short|neutral",
                    }
                ],
                "overall_pattern_quality": "strong|moderate|weak",
                "nearest_support": float,
                "nearest_resistance": float,
                "recommendation": str,
            }
        """
        recent_prices = price_data[-50:] if len(price_data) > 50 else price_data
        vol_recent = volume_data[-50:] if volume_data and len(volume_data) > 50 else None

        price_stats = {
            "current": recent_prices[-1],
            "high_50": max(recent_prices),
            "low_50": min(recent_prices),
            "change_20d": (recent_prices[-1] / recent_prices[-20] - 1) * 100 if len(recent_prices) >= 20 else 0,
            "change_50d": (recent_prices[-1] / recent_prices[0] - 1) * 100 if len(recent_prices) > 1 else 0,
        }

        prompt = f"""Identify technical patterns for {symbol} on {timeframe}:

Price Stats (last 50 periods):
{json.dumps(price_stats)}

Recent Prices: {recent_prices[-10:]}
Volume Trend: {'increasing' if vol_recent and vol_recent[-1] > vol_recent[-5] else 'decreasing' if vol_recent else 'unknown'}

Identify:
- Chart patterns (double bottom, head & shoulders, breakouts, wedges, flags, triangles, etc.)
- Support/resistance levels
- Trend quality

Return JSON with:
- patterns: list of {{"type": str, "confidence": 0-1, "price_target": float, "trade_setup": "long|short|neutral"}}
- overall_pattern_quality: "strong", "moderate", or "weak"
- nearest_support: price level
- nearest_resistance: price level
- recommendation: actionable trading setup

Guidelines:
- Strong patterns = clear price target + volume confirmation
- Support/resistance = prior swing highs/lows
- Only include high-confidence patterns (>0.65)"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a technical analyst for {symbol}. Identify reliable chart patterns.",
                temperature=0.3,
                cache_key=f"patterns_{symbol}_{timeframe}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Pattern identification failed for {symbol}: {e}")
            return {
                "patterns": [],
                "overall_pattern_quality": "weak",
                "nearest_support": min(recent_prices),
                "nearest_resistance": max(recent_prices),
                "recommendation": "Unable to analyze",
                "error": str(e),
            }

    def assess_market_regime(
        self,
        symbol: str,
        market_indicators: dict,
    ) -> dict:
        """
        Identify current market regime and regime probability.

        Args:
            symbol: Stock symbol
            market_indicators: {
                "trend": "up|down|sideways",
                "volatility": "high|normal|low",
                "breadth": "strong|weak",
                "correlation": float (-1 to 1),
                "trend_strength": float (0-1),
            }

        Returns:
            {
                "regime": "strong_uptrend|uptrend|range_bound|downtrend|strong_downtrend",
                "regime_probability": {
                    "strong_uptrend": 0.0-1.0,
                    "uptrend": 0.0-1.0,
                    "range_bound": 0.0-1.0,
                    "downtrend": 0.0-1.0,
                    "strong_downtrend": 0.0-1.0,
                },
                "regime_change_risk": 0.0-1.0,
                "strategy_implications": str,
                "positioning": "aggressive_long|long|neutral|short|aggressive_short",
            }
        """
        prompt = f"""Assess market regime for {symbol}:

Market Indicators:
{json.dumps(market_indicators)}

Evaluate current regime based on:
- Trend direction and strength
- Volatility environment
- Market breadth
- Correlation patterns

Return JSON with:
- regime: current regime classification
- regime_probability: probabilities for each regime (should sum ~100%)
- regime_change_risk: 0-1 probability of regime change in next 20 days
- strategy_implications: how to trade in this regime
- positioning: recommended trading posture

Regimes:
- Strong uptrend: sustained higher highs, strong breadth
- Uptrend: general up direction but consolidations
- Range bound: oscillating between support/resistance
- Downtrend: lower lows, weak breadth
- Strong downtrend: sustained decline, panic selling"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a market regime analyst. Classify {symbol} market conditions precisely.",
                temperature=0.3,
                cache_key=f"regime_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Regime assessment failed for {symbol}: {e}")
            return {
                "regime": "range_bound",
                "regime_probability": {
                    "strong_uptrend": 0.1,
                    "uptrend": 0.2,
                    "range_bound": 0.4,
                    "downtrend": 0.2,
                    "strong_downtrend": 0.1,
                },
                "regime_change_risk": 0.3,
                "strategy_implications": "Unable to assess",
                "positioning": "neutral",
                "error": str(e),
            }

    def analyze_volatility(
        self,
        symbol: str,
        realized_vol: float,
        implied_vol: Optional[float] = None,
        vol_percentile: float = 50,
    ) -> dict:
        """
        Analyze volatility and forecast future vol environment.

        Returns:
            {
                "vol_regime": "low|normal|high|extreme",
                "realized_vs_implied": "underestimated|fair|overestimated",
                "vol_forecast": {
                    "1week": "increasing|stable|decreasing",
                    "1month": "increasing|stable|decreasing",
                    "direction": "neutral_or_up|up|down",
                },
                "opportunity": "vol_expansion|vol_compression|none",
                "option_strategy": "bull_call|iron_condor|strangle|...",
                "position_sizing": float (0.5-2.0x base size),
            }
        """
        prompt = f"""Analyze volatility for {symbol}:

Realized Volatility: {realized_vol:.2f}%
Implied Volatility: {implied_vol:.2f}% if implied_vol else 'N/A'
Vol Percentile (20d): {vol_percentile}%

Assess:
- Vol regime classification
- RV vs IV spread (if available)
- Near-term vol direction
- Vol mean reversion likelihood

Return JSON with:
- vol_regime: "low", "normal", "high", or "extreme"
- realized_vs_implied: "underestimated" (RV > IV), "fair", or "overestimated" (RV < IV)
- vol_forecast: {{"1week": str, "1month": str, "direction": "neutral_or_up" or "up" or "down"}}
- opportunity: "vol_expansion", "vol_compression", or "none"
- option_strategy: recommended options strategy
- position_sizing: 0.5-2.0x multiplier for position sizing

Guidelines:
- Low vol + high percentile = compression likely
- High vol + high percentile = mean reversion likely
- Earnings/events = vol expansion expected"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a volatility specialist for {symbol}. Forecast vol regime.",
                temperature=0.3,
                cache_key=f"vol_analysis_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Volatility analysis failed for {symbol}: {e}")
            return {
                "vol_regime": "normal",
                "realized_vs_implied": "fair",
                "vol_forecast": {"1week": "stable", "1month": "stable", "direction": "neutral_or_up"},
                "opportunity": "none",
                "option_strategy": "neutral",
                "position_sizing": 1.0,
                "error": str(e),
            }

    def identify_divergences(
        self,
        symbol: str,
        price_data: List[float],
        indicator_data: Dict[str, List[float]],
        lookback_periods: int = 50,
    ) -> dict:
        """
        Identify technical divergences between price and indicators.

        Args:
            symbol: Stock symbol
            price_data: Recent price history
            indicator_data: {
                "rsi": List[float],
                "macd": List[float],
                "volume": List[float],
                ...
            }

        Returns:
            {
                "divergences": [
                    {
                        "type": "bullish|bearish",
                        "indicator": "rsi|macd|volume|...",
                        "strength": "strong|moderate|weak",
                        "implication": str,
                    }
                ],
                "strongest_divergence": str,
                "trading_implication": str,
                "probability_reversal": 0.0-1.0,
            }
        """
        recent_prices = price_data[-lookback_periods:]

        prompt = f"""Identify divergences for {symbol}:

Price Movement (last {lookback_periods} periods):
- Recent: {recent_prices[-5:]}
- Trend: {'higher highs' if recent_prices[-1] > max(recent_prices[:-1]) else 'lower lows' if recent_prices[-1] < min(recent_prices[:-1]) else 'sideways'}

Indicators:
{json.dumps({k: v[-10:] for k, v in indicator_data.items()}, indent=2)}

Identify divergences (price vs indicator not confirming):
- Bullish: price makes lower low, indicator makes higher low (reversal signal)
- Bearish: price makes higher high, indicator makes lower high (reversal signal)

Return JSON with:
- divergences: list of {{"type": "bullish|bearish", "indicator": str, "strength": "strong|moderate|weak"}}
- strongest_divergence: which divergence is most actionable
- trading_implication: what does strongest divergence suggest
- probability_reversal: likelihood of price reversal in next 1-5 days

Guidelines:
- Multiple divergences = higher reversal probability
- Strong divergences = volume or momentum confirmation
- Weak divergences = noise, often unreliable"""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a divergence specialist. Identify actionable {symbol} reversals.",
                temperature=0.3,
                cache_key=f"divergences_{symbol}",
            )
            result = self.llm.parse_json_response(response)
            return result
        except Exception as e:
            logger.error(f"Divergence identification failed for {symbol}: {e}")
            return {
                "divergences": [],
                "strongest_divergence": "none",
                "trading_implication": "Unable to identify divergences",
                "probability_reversal": 0.0,
                "error": str(e),
            }
