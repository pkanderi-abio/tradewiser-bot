---
name: implement-trading-strategy
description: "Guided workflow to implement a new trading strategy from concept to deployment"
---

# Implement Trading Strategy

Use this prompt to walk through implementing a complete trading strategy.

## Parameters

- **Strategy Name**: Name of the new trading strategy (e.g., "momentum-oscillator", "rsi-breakout")
- **Market Data**: Indicators/data needed (e.g., RSI, MACD, volume, price action)
- **Entry Signals**: Conditions that trigger a buy signal
- **Exit Signals**: Conditions that trigger a sell signal
- **Risk Management**: Stop-loss %, position size limits, max drawdown

## Workflow

### Phase 1: Define the Strategy
1. Review strategy requirements and parameters
2. Identify market data dependencies
3. Define entry and exit conditions clearly
4. Establish risk management rules

### Phase 2: Implement AI Advisor Logic
1. Add strategy function to `app/services/ai_advisor.py`
2. Implement signal generation with confidence scores
3. Add indicator calculations or use external libraries
4. Write unit tests for signal accuracy
5. Validate with historical data sample

### Phase 3: Integrate with Trading Engine
1. Update `app/services/trading_engine.py` to handle new signals
2. Add stop-loss and position limit checks
3. Implement order execution logic
4. Add monitoring/logging for strategy execution

### Phase 4: Create Endpoints
1. Add GET endpoint to retrieve strategy status
2. Add POST endpoint to enable/disable strategy
3. Add configuration endpoint for strategy parameters
4. Test endpoints with sample market data

### Phase 5: Write Tests
1. Unit tests for strategy signal generation
2. Integration tests for AI → Engine → Alpaca flow
3. Edge case tests (gaps, halts, volatility)
4. Historical backtest validation

### Phase 6: Deploy & Monitor
1. Enable strategy in configuration
2. Run comprehensive test suite
3. Monitor live execution for 48 hours
4. Track win rate, average return, drawdown
5. Adjust parameters if needed

## Example: RSI Breakout Strategy

```python
# Entry Signal: RSI > 70 (overbought) AND price breaks resistance
# Exit Signal: RSI < 30 (oversold) OR stop-loss triggered
# Risk: 2% stop-loss, max 5 concurrent trades

async def get_rsi_breakout_signal(symbol: str) -> Optional[TradingSignal]:
    """Generate RSI breakout trading signal."""
    bars = await alpaca_client.get_bars(symbol, timeframe='1H', limit=14)
    rsi = calculate_rsi(bars.close, period=14)
    
    if rsi[-1] > 70 and bars.close[-1] > bars.close[-2]:
        return TradingSignal(
            symbol=symbol,
            action='buy',
            confidence=0.85,
            entry_price=bars.close[-1],
            stop_loss=bars.close[-1] * 0.98,  # 2% below entry
            target_price=bars.close[-1] * 1.05  # 5% target
        )
    return None
```

## Validation Checklist

- [ ] Strategy logic is documented
- [ ] Entry and exit conditions are clear
- [ ] Stop-loss and position limits are defined
- [ ] Unit tests cover 80%+ of strategy code
- [ ] Integration tests validate full flow
- [ ] Edge cases tested (gaps, halts, low volume)
- [ ] Endpoints tested with sample requests
- [ ] Historical backtest shows positive expectancy
- [ ] Configuration is parameterized (not hardcoded)
- [ ] Logging captures all strategy decisions
