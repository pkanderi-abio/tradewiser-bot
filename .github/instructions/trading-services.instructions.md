---
name: trading-services
description: "Use when: working on trading_engine.py, ai_advisor.py, or alpaca_client.py. Guides implementation of trading logic, AI recommendations, and broker integration with emphasis on autonomy, testing, and fail-safes."
applyTo: "app/services/trading_engine.py|app/services/ai_advisor.py|app/services/alpaca_client.py"
---

# Trading Services Guidelines

## Core Principles

### Trading Engine (`trading_engine.py`)
- All trading decisions must be logged with timestamp, strategy, signal, and execution status
- Implement fail-safes: position limits, stop-loss checks, insufficient fund detection
- Separate signal evaluation from order execution for clarity and testing
- Use async patterns for non-blocking trade execution
- Always validate order parameters before sending to Alpaca

### AI Advisor (`ai_advisor.py`)
- Recommendations should be deterministic (same market data → same signal)
- Include confidence scores or signal strength in all recommendations
- Document the strategy logic: what indicators/factors drive the decision
- Return structured data (recommendation objects, not raw values)
- Implement timeout handling for external data fetches

### Alpaca Client (`alpaca_client.py`)
- Wrap Alpaca API calls with error handling and retry logic
- Cache market data appropriately (respect rate limits)
- Handle account state edge cases (halted securities, trading halts, insufficient margin)
- Keep Alpaca credential management centralized (config.py, environment variables)
- Log all API calls for audit trail

## Testing Expectations

Every trading service change requires:
- Unit tests for individual functions (logic, edge cases)
- Integration tests for service interactions (AI → Engine → Alpaca)
- Mock Alpaca API for deterministic testing
- Test both happy path and failure scenarios

## Code Patterns

```python
# Good: Clear separation of concerns
class TradingEngine:
    def evaluate_signal(self, signal: TradingSignal) -> bool:
        """Validate signal meets entry criteria."""
        return signal.confidence > self.min_confidence
    
    async def execute_trade(self, signal: TradingSignal) -> TradeResult:
        """Execute validated signal."""
        try:
            order = await self.alpaca_client.place_order(...)
            self.logger.info(f"Trade executed: {order}")
            return TradeResult.success(order)
        except InsufficientFundsError as e:
            self.logger.warning(f"Insufficient funds: {e}")
            return TradeResult.failed(reason="insufficient_funds")

# Good: Configuration, not hardcoding
self.position_limit = config.get("trading.max_position_size", default=1000)
self.stop_loss_pct = config.get("trading.stop_loss_percent", default=5)
```

## Common Issues to Avoid

❌ **Hardcoded values** — Use config files  
❌ **Silent failures** — Always log errors  
❌ **No order validation** — Check funds, halted stocks, position limits before executing  
❌ **Untested trading paths** — Every trade code path must have tests  
❌ **Race conditions** — Use locking for concurrent trade execution  
❌ **Missing error recovery** — Handle network timeouts, API rate limits, broker errors

## Autonomous Trading Checklist

When implementing autonomous features:
- [ ] Strategy logic is deterministic
- [ ] AI confidence/signal strength is tracked
- [ ] Trading engine validates before execution
- [ ] Order status is monitored (filled, partial, rejected)
- [ ] Alpaca errors are gracefully handled
- [ ] Position limits and stop-losses are enforced
- [ ] All decisions are logged for audit
- [ ] Tests cover at least 80% of trading paths
- [ ] Documentation explains the trading flow
