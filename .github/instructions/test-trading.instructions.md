---
name: test-trading
description: "Use when: writing or maintaining test_trading_engine.py, test_alpaca_client.py, or trade-related tests. Enforces testing standards for autonomous trading logic."
applyTo: "tests/test_trading_engine.py|tests/test_alpaca_client.py|tests/test_routes_trades.py"
---

# Trading Tests Guidelines

## Test Structure

Each trading test file should organize tests by:
1. **Unit Tests** — Individual function logic
2. **Integration Tests** — Component interactions
3. **Edge Case Tests** — Failure scenarios

## Required Test Coverage

### Trading Engine Tests
```python
class TestTradingEngine:
    # Unit: Signal validation
    def test_evaluate_signal_high_confidence()
    def test_evaluate_signal_low_confidence_rejected()
    def test_evaluate_signal_market_halted()
    
    # Unit: Position management
    def test_position_limit_enforced()
    def test_stop_loss_triggered()
    def test_insufficient_funds_detected()
    
    # Integration: Execute flow
    def test_execute_trade_end_to_end()
    def test_execute_trade_with_alpaca_failure()
    def test_execute_trade_with_partial_fill()
    
    # Edge cases
    def test_concurrent_trades_handled()
    def test_network_timeout_recovery()
    def test_order_status_updates()
```

### Alpaca Client Tests
```python
class TestAlpacaClient:
    # Unit: API wrapping
    def test_place_order_formats_correctly()
    def test_get_quotes_parses_response()
    def test_get_account_info_returns_state()
    
    # Error handling
    def test_rate_limit_retry()
    def test_insufficient_margin_error()
    def test_halted_security_error()
    def test_api_timeout_handled()
    
    # Integration: Real-like scenarios
    def test_place_and_cancel_order()
    def test_check_account_readiness()
```

## Mocking Patterns

```python
import pytest
from unittest.mock import Mock, patch, AsyncMock

@pytest.fixture
def mock_alpaca_client():
    """Mock Alpaca API responses."""
    client = Mock()
    client.get_account = Mock(return_value={'buying_power': 10000})
    client.place_order = AsyncMock(return_value={'id': 'order123', 'status': 'filled'})
    return client

@pytest.fixture
def mock_ai_advisor():
    """Mock AI recommendations."""
    advisor = Mock()
    advisor.get_recommendation = Mock(return_value={
        'symbol': 'AAPL',
        'action': 'buy',
        'confidence': 0.85,
        'reason': 'momentum signal'
    })
    return advisor

def test_execute_trade_with_mocks(mock_alpaca_client, mock_ai_advisor):
    engine = TradingEngine(mock_alpaca_client, mock_ai_advisor)
    result = engine.execute_trade('AAPL', 10, 'buy')
    assert result.status == 'executed'
    mock_alpaca_client.place_order.assert_called_once()
```

## Test Data Patterns

```python
# Use fixtures for consistent test data
@pytest.fixture
def sample_trading_signal():
    return TradingSignal(
        symbol='AAPL',
        action='buy',
        confidence=0.90,
        entry_price=150.25,
        stop_loss=145.24,
        target_price=160.00
    )

@pytest.fixture
def sample_account_state():
    return {
        'buying_power': 50000,
        'portfolio_value': 100000,
        'positions': [{'symbol': 'MSFT', 'qty': 10}]
    }
```

## Running Tests

```bash
# Run all trading tests
pytest tests/test_trading_engine.py tests/test_alpaca_client.py -v

# Run with coverage
pytest tests/test_trading_engine.py --cov=app/services/trading_engine

# Run specific test class
pytest tests/test_trading_engine.py::TestTradingEngine::test_execute_trade_end_to_end -v
```

## Coverage Goals

- **Minimum 80%** code coverage for trading engine
- **100%** coverage for critical paths (order execution, risk checks)
- All error handlers must have tests
- All async operations must have tests

## Common Testing Mistakes

❌ Testing without mocks (hitting real Alpaca API)  
❌ Hardcoding test data instead of fixtures  
❌ Not testing error cases  
❌ Ignoring timing/async issues  
❌ Over-mocking (mocking too much makes tests brittle)  
✅ Use fixtures for reusable test data  
✅ Mock external dependencies (Alpaca, AI)  
✅ Test both success and failure paths  
✅ Use parametrized tests for variations  
