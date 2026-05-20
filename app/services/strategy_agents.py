"""
AI Trading Strategy Agents — Specialized AI agents for different trading strategies.

Each agent uses LLM to execute a specific trading strategy:
  - Momentum Trading Agent
  - Mean Reversion Agent
  - Breakout Agent
  - Earnings Play Agent
  - Sector Rotation Agent
  - Risk Management Agent
"""

import json
from typing import Dict, List, Optional
from enum import Enum

from app.core.config import settings
from app.core.logger import logger
from app.services.llm_service import LLMService


class StrategyType(str, Enum):
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"
    EARNINGS = "earnings"
    SECTOR_ROTATION = "sector_rotation"
    RISK_MANAGEMENT = "risk_management"


class TradingStrategyAgent:
    """Base class for AI-powered trading strategy agents."""

    def __init__(self, strategy_type: StrategyType):
        self.strategy_type = strategy_type
        self.llm = LLMService()
        self.name = strategy_type.value
        logger.info(f"Initialized {self.name} strategy agent")

    def execute(self, market_data: dict) -> dict:
        """Execute strategy and return trading signal."""
        raise NotImplementedError


class MomentumAgent(TradingStrategyAgent):
    """
    Momentum Trading Agent — Trades in direction of strong trends.
    
    Buys when: Strong uptrend, RSI > 70, volume increasing
    Sells when: Trend breaks, RSI crosses below key level, volume declining
    """

    def __init__(self):
        super().__init__(StrategyType.MOMENTUM)

    def execute(self, market_data: dict) -> dict:
        """
        Execute momentum strategy.
        
        Args:
            market_data: {
                "symbol": str,
                "price": float,
                "rsi": float,
                "macd": float,
                "sma20": float,
                "sma50": float,
                "volume": int,
                "volume_sma": int,
                "recent_prices": List[float],
            }
        
        Returns:
            {
                "signal": "BUY|SELL|HOLD",
                "confidence": 0-1,
                "entry_price": float,
                "exit_price": float,
                "stop_loss": float,
                "take_profit": float,
                "reasoning": str,
            }
        """
        symbol = market_data.get("symbol", "UNKNOWN")
        price = market_data.get("price", 0)
        rsi = market_data.get("rsi", 50)
        macd = market_data.get("macd", 0)
        volume_ratio = (market_data.get("volume", 0) / market_data.get("volume_sma", 1))

        prompt = f"""Execute momentum strategy for {symbol} at ${price:.2f}:

Technical Indicators:
- RSI: {rsi:.1f}
- MACD: {macd:.2f}
- Volume Ratio: {volume_ratio:.2f}x average
- SMA20/50: forming {'bullish' if market_data.get('sma20', 0) > market_data.get('sma50', 0) else 'bearish'} cross

Momentum Strategy Rules:
- BUY: RSI > 60, MACD positive, volume increasing, price above SMA20
- SELL: RSI < 40, MACD negative, or volume declining significantly
- HOLD: Uncertain signals

Return JSON with:
- signal: "BUY", "SELL", or "HOLD"
- confidence: 0-1
- entry_price: recommended entry (for BUY signals)
- exit_price: target exit price
- stop_loss: stop loss price
- take_profit: profit target
- reasoning: brief explanation

Focus on momentum confirmation through volume."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a momentum trading expert for {symbol}. Execute trend-following strategy.",
                temperature=0.3,
                cache_key=f"momentum_{symbol}_{price}",
            )
            signal = self.llm.parse_json_response(response)
            signal["strategy"] = self.name
            return signal
        except Exception as e:
            logger.error(f"Momentum agent error: {e}")
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "reasoning": f"Agent error: {e}",
                "strategy": self.name,
            }


class MeanReversionAgent(TradingStrategyAgent):
    """
    Mean Reversion Agent — Trades against extremes (overbought/oversold).
    
    Buys when: Price oversold (RSI < 30), near support, positive divergence
    Sells when: Price overbought (RSI > 70), near resistance, negative divergence
    """

    def __init__(self):
        super().__init__(StrategyType.MEAN_REVERSION)

    def execute(self, market_data: dict) -> dict:
        """Execute mean reversion strategy."""
        symbol = market_data.get("symbol", "UNKNOWN")
        price = market_data.get("price", 0)
        rsi = market_data.get("rsi", 50)
        bb_width = market_data.get("bollinger_width", 0)
        support = market_data.get("support_level", price * 0.95)
        resistance = market_data.get("resistance_level", price * 1.05)

        prompt = f"""Execute mean reversion strategy for {symbol} at ${price:.2f}:

Price Action:
- Current RSI: {rsi:.1f}
- Support Level: ${support:.2f}
- Resistance Level: ${resistance:.2f}
- Bollinger Band Width: {bb_width:.2f}

Mean Reversion Rules:
- BUY: RSI < 30 (oversold), price near support, Bollinger bands narrow
- SELL: RSI > 70 (overbought), price near resistance, preparation for pullback
- HOLD: RSI in neutral zone 30-70

Return JSON with:
- signal: "BUY", "SELL", or "HOLD"
- confidence: 0-1 (higher for extreme RSI readings)
- entry_price: recommended entry
- exit_price: target exit (mean reversion target)
- stop_loss: stop loss price
- take_profit: profit target
- mean_reversion_target: where we expect price to revert to
- reasoning: brief explanation

Focus on extremes and support/resistance levels."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a mean reversion specialist for {symbol}. Trade extremes.",
                temperature=0.3,
                cache_key=f"mean_reversion_{symbol}_{price}",
            )
            signal = self.llm.parse_json_response(response)
            signal["strategy"] = self.name
            return signal
        except Exception as e:
            logger.error(f"Mean reversion agent error: {e}")
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "reasoning": f"Agent error: {e}",
                "strategy": self.name,
            }


class BreakoutAgent(TradingStrategyAgent):
    """
    Breakout Agent — Trades consolidation breakouts.
    
    Buys when: Price breaks above resistance with volume
    Sells when: Price breaks below support with volume
    """

    def __init__(self):
        super().__init__(StrategyType.BREAKOUT)

    def execute(self, market_data: dict) -> dict:
        """Execute breakout strategy."""
        symbol = market_data.get("symbol", "UNKNOWN")
        price = market_data.get("price", 0)
        resistance = market_data.get("resistance_level", price * 1.05)
        support = market_data.get("support_level", price * 0.95)
        volume = market_data.get("volume", 0)
        volume_sma = market_data.get("volume_sma", 1)
        range_high = market_data.get("range_high", resistance)
        range_low = market_data.get("range_low", support)

        prompt = f"""Execute breakout strategy for {symbol} at ${price:.2f}:

Consolidation Range:
- Range High: ${range_high:.2f}
- Range Low: ${range_low:.2f}
- Current Price: ${price:.2f}
- Volume: {volume:,} ({volume/volume_sma:.2f}x average)

Breakout Rules:
- BUY: Price breaks above resistance {range_high:.2f} with volume > 1.5x average
- SELL: Price breaks below support {range_low:.2f} with volume > 1.5x average
- HOLD: Price in consolidation range

Return JSON with:
- signal: "BUY", "SELL", or "HOLD"
- confidence: 0-1 (higher with volume confirmation)
- entry_price: breakout entry price
- exit_price: target after breakout
- stop_loss: inside range (opposite breakout level)
- take_profit: distance = range height
- range_breakout_target: price target based on range
- reasoning: brief explanation

Volume confirmation is critical."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a breakout trader for {symbol}. Trade consolidation breakouts.",
                temperature=0.3,
                cache_key=f"breakout_{symbol}_{int(price)}",
            )
            signal = self.llm.parse_json_response(response)
            signal["strategy"] = self.name
            return signal
        except Exception as e:
            logger.error(f"Breakout agent error: {e}")
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "reasoning": f"Agent error: {e}",
                "strategy": self.name,
            }


class EarningsAgent(TradingStrategyAgent):
    """
    Earnings Play Agent — Trades around earnings announcements.
    
    Buys when: Earnings expected beat with positive guidance
    Sells when: Earnings expected miss or guidance cut
    """

    def __init__(self):
        super().__init__(StrategyType.EARNINGS)

    def execute(self, market_data: dict) -> dict:
        """Execute earnings play strategy."""
        symbol = market_data.get("symbol", "UNKNOWN")
        price = market_data.get("price", 0)
        earnings_date = market_data.get("earnings_date", "unknown")
        iv_rank = market_data.get("iv_rank", 50)
        beat_probability = market_data.get("beat_probability", 0.5)
        analyst_rating = market_data.get("analyst_rating", "hold")

        prompt = f"""Execute earnings play strategy for {symbol} at ${price:.2f}:

Earnings Context:
- Earnings Date: {earnings_date}
- IV Rank: {iv_rank}% (vol expectations)
- Beat Probability: {beat_probability:.0%}
- Analyst Consensus: {analyst_rating}
- Expected Move: ~{iv_rank/100*price:.2f}

Earnings Play Rules:
- BUY: High beat probability (>65%), analyst upgrades, low IV
- SELL/SHORT: Miss expected (>60%), analyst downgrades, IV spike
- HOLD: Near 50/50, awaiting catalyst

Return JSON with:
- signal: "BUY", "SELL", "SHORT", or "HOLD"
- confidence: 0-1
- entry_price: entry before earnings
- exit_price: target after earnings move
- stop_loss: stop loss
- take_profit: profit target
- expected_move: estimated move post-earnings
- timing: "before_earnings", "day_of", "after_earnings"
- reasoning: brief explanation

Consider volatility expansion."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are an earnings trader for {symbol}. Trade earnings catalysts.",
                temperature=0.3,
                cache_key=f"earnings_{symbol}",
            )
            signal = self.llm.parse_json_response(response)
            signal["strategy"] = self.name
            return signal
        except Exception as e:
            logger.error(f"Earnings agent error: {e}")
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "reasoning": f"Agent error: {e}",
                "strategy": self.name,
            }


class SectorRotationAgent(TradingStrategyAgent):
    """
    Sector Rotation Agent — Rotates between best/worst sector performers.
    
    Buys when: Sector showing strongest relative strength
    Sells when: Sector underperforming market
    """

    def __init__(self):
        super().__init__(StrategyType.SECTOR_ROTATION)

    def execute(self, market_data: dict) -> dict:
        """Execute sector rotation strategy."""
        symbol = market_data.get("symbol", "UNKNOWN")
        price = market_data.get("price", 0)
        sector = market_data.get("sector", "unknown")
        sector_performance = market_data.get("sector_performance", 0)
        market_performance = market_data.get("market_performance", 0)
        relative_strength = sector_performance - market_performance
        sector_rank = market_data.get("sector_rank", 5)  # Out of 11 sectors

        prompt = f"""Execute sector rotation strategy for {symbol} (${price:.2f}) in {sector}:

Sector Performance:
- Sector YTD: {sector_performance:.2f}%
- Market YTD: {market_performance:.2f}%
- Relative Strength: {relative_strength:.2f}%
- Sector Rank: {sector_rank}/11 (1 = best)

Sector Rotation Rules:
- BUY: Sector in top 3 performers, outperforming by >2%, positive momentum
- SELL: Sector in bottom 3 performers, underperforming by >2%, negative momentum
- HOLD: Mid-pack sector, unclear trend

Return JSON with:
- signal: "BUY", "SELL", or "HOLD"
- confidence: 0-1
- entry_price: entry price for sector play
- exit_price: target exit
- stop_loss: stop loss
- take_profit: profit target
- sector_rotation_target: next sector to rotate into
- reasoning: brief explanation

Consider market regime for sector rotation success."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt=f"You are a sector strategist. Execute sector rotation for {symbol}.",
                temperature=0.3,
                cache_key=f"sector_{sector}_{symbol}",
            )
            signal = self.llm.parse_json_response(response)
            signal["strategy"] = self.name
            return signal
        except Exception as e:
            logger.error(f"Sector rotation agent error: {e}")
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "reasoning": f"Agent error: {e}",
                "strategy": self.name,
            }


class RiskManagementAgent(TradingStrategyAgent):
    """
    Risk Management Agent — Optimizes position sizing and risk controls.
    
    Adjusts position size based on volatility, drawdown, correlation, and portfolio risk.
    """

    def __init__(self):
        super().__init__(StrategyType.RISK_MANAGEMENT)

    def execute(self, portfolio_data: dict) -> dict:
        """
        Execute risk management strategy.
        
        Args:
            portfolio_data: {
                "total_capital": float,
                "available_capital": float,
                "current_drawdown": float,  # -5.5 means -5.5%
                "daily_loss": float,
                "volatility": float,
                "correlation_with_market": float,
                "positions": int,
                "max_position_size": float,
                "risk_per_trade": float,
            }
        
        Returns:
            {
                "position_size_multiplier": float,  # 0.5 to 2.0
                "risk_level": "low|medium|high|critical",
                "recommendations": [...],
                "stop_trading": bool,
                "reasoning": str,
            }
        """
        total_capital = portfolio_data.get("total_capital", 100000)
        drawdown = portfolio_data.get("current_drawdown", 0)
        daily_loss = portfolio_data.get("daily_loss", 0)
        volatility = portfolio_data.get("volatility", 20)
        positions = portfolio_data.get("positions", 0)

        prompt = f"""Assess risk and optimize position sizing:

Portfolio State:
- Total Capital: ${total_capital:,.0f}
- Current Drawdown: {drawdown:.1f}%
- Daily Loss: ${daily_loss:,.0f}
- Current Volatility: {volatility:.1f}%
- Open Positions: {positions}

Risk Management Rules:
- CRITICAL: Drawdown > 10% OR daily loss > 5% of capital → Reduce size 50%
- HIGH: Drawdown 5-10% OR daily loss 2-5% → Reduce size to 70%
- MEDIUM: Drawdown 0-5% OR daily loss <2% → Normal size
- LOW: Drawdown negative (profit) AND daily loss minimal → Can increase 20%

Return JSON with:
- position_size_multiplier: 0.5 (very conservative) to 2.0 (aggressive)
- risk_level: "low", "medium", "high", or "critical"
- recommendations: list of specific actions (stop trading, reduce size, diversify, etc.)
- stop_trading: bool - should we stop trading immediately?
- max_loss_per_trade: recommended max loss per trade
- reasoning: brief explanation

Prioritize capital preservation."""

        try:
            response = self.llm.query(
                prompt,
                system_prompt="You are a risk manager. Optimize position sizing and capital preservation.",
                temperature=0.2,  # Very conservative
                cache_key="risk_management",
            )
            signal = self.llm.parse_json_response(response)
            signal["strategy"] = self.name
            return signal
        except Exception as e:
            logger.error(f"Risk management agent error: {e}")
            return {
                "position_size_multiplier": 0.5,
                "risk_level": "high",
                "recommendations": ["Agent error - reducing position size"],
                "stop_trading": True,
                "reasoning": f"Agent error: {e}",
                "strategy": self.name,
            }


# Factory function to get strategy agent
def get_strategy_agent(strategy_type: str) -> TradingStrategyAgent:
    """Get the appropriate strategy agent."""
    agents = {
        StrategyType.MOMENTUM.value: MomentumAgent,
        StrategyType.MEAN_REVERSION.value: MeanReversionAgent,
        StrategyType.BREAKOUT.value: BreakoutAgent,
        StrategyType.EARNINGS.value: EarningsAgent,
        StrategyType.SECTOR_ROTATION.value: SectorRotationAgent,
        StrategyType.RISK_MANAGEMENT.value: RiskManagementAgent,
    }

    agent_class = agents.get(strategy_type)
    if not agent_class:
        raise ValueError(f"Unknown strategy type: {strategy_type}")

    return agent_class()
