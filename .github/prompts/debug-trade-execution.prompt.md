---
name: debug-trade-execution
description: "Systematic approach to diagnose and fix trading execution issues"
---

# Debug Trade Execution

Use this prompt to troubleshoot trading logic failures.

## Parameters

- **Issue**: What's happening incorrectly (e.g., "trades not executing", "wrong position size", "stop-loss not working")
- **Timeframe**: When did the issue start?
- **Affected Symbols**: Which securities are impacted?
- **Frequency**: Always happens or intermittent?

## Diagnostic Workflow

### Step 1: Gather Context
```
Questions to answer:
- Does the AI advisor generate signals? (Check logs)
- Do signals reach the trading engine? (Check log timestamps)
- Is the trading engine validating correctly? (Check validation logs)
- Is Alpaca API responding? (Check API error logs)
- Are there auth/credential issues? (Check Alpaca error codes)
```

### Step 2: Check AI Advisor Layer
```python
# Check if signals are being generated
logs = review_logs("ai_advisor", timeframe="last_1_hour")
for log in logs:
    print(f"{log.timestamp}: {log.symbol} -> {log.signal}")

# Check signal confidence and reasoning
assert signal.confidence > engine.min_confidence
```

**Possible Issues:**
- Strategy logic returning None (no signals)
- Confidence too low (default threshold not met)
- Market data stale or unavailable
- Indicators calculating incorrectly

### Step 3: Check Trading Engine Layer
```python
# Verify validation logic
assert trading_engine.validate_order(symbol, quantity, side)

# Check position limits
assert current_positions + new_position <= position_limit

# Check stop-loss setup
assert stop_loss_price < entry_price (for buy)
```

**Possible Issues:**
- Position limit preventing execution
- Stop-loss not triggered
- Order validation rejecting valid signals
- Concurrent trade race conditions

### Step 4: Check Alpaca Integration
```python
# Verify credentials
assert alpaca_client.is_authenticated()

# Check account state
account = alpaca_client.get_account()
assert account['buying_power'] > order_value

# Verify order placement
order = alpaca_client.place_order(...)
print(f"Order ID: {order.id}, Status: {order.status}")
```

**Possible Issues:**
- API rate limit exceeded
- Account not connected
- Insufficient margin/buying power
- Security halted or delisted
- Invalid order parameters

### Step 5: Run Targeted Tests
```bash
# Test AI advisor signals
pytest tests/test_ai_advisor.py::test_signal_generation -v -s

# Test trading engine validation
pytest tests/test_trading_engine.py::test_validate_order -v -s

# Test Alpaca integration
pytest tests/test_alpaca_client.py::test_place_order -v -s

# Run integration test end-to-end
pytest tests/test_trading_engine.py::test_execute_trade_end_to_end -v -s
```

## Common Issues & Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| No trades executing | AI not generating signals | Check market data, indicator calculation, confidence threshold |
| Wrong position size | Position limit too low | Review config.trading.max_position_size |
| Stop-loss not triggering | Stop-loss logic missing | Verify stop-loss price calculation and trigger condition |
| Alpaca auth failing | Expired credentials | Regenerate Alpaca API keys in .env |
| Insufficient funds error | Position size too large | Reduce max_position_size or check available buying power |
| Rate limit errors | Too many API calls | Add retry logic, implement backoff, throttle requests |
| Halted security error | Stock trading halted | Add check for halted status before order placement |
| Order stuck pending | Fill never received | Implement order timeout, add cancel logic, check liquidity |

## Verification Steps

After fixing:

1. **Enable debug logging** for the affected component
2. **Run targeted test** to reproduce the fix
3. **Run full test suite** to ensure no regressions
4. **Monitor live execution** for 24 hours
5. **Document the issue and fix** for future reference

## Log Analysis Example

```bash
# Check trading engine logs
tail -f logs/trading_engine.log | grep -E "ERROR|WARN|signal"

# Search for specific symbol
grep "AAPL" logs/trading_engine.log | tail -20

# Check timestamps to find execution gaps
grep "signal_generated\|order_placed\|order_filled" logs/trading_engine.log
```
