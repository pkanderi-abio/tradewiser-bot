---
name: routes-trades
description: "Use when: working on routes/trades.py or related trade endpoint logic. Ensures trade endpoints are properly authenticated, validated, and integrated with trading engine."
applyTo: "app/routes/trades.py"
---

# Trade Routes Guidelines

## Endpoint Standards

All trade endpoints must:
1. **Authenticate** — Verify user credentials via auth service
2. **Validate** — Check request parameters (symbol, quantity, type)
3. **Check State** — Verify account readiness (connected to Alpaca, sufficient funds)
4. **Execute Safely** — Call trading engine with error handling
5. **Log & Return** — Structured responses with status and metadata

## Common Endpoints

### POST `/trades` — Place a Trade
```
Request: { symbol, quantity, side (buy/sell), type (market/limit), price? }
Validation:
  - symbol exists and not halted
  - quantity > 0 and within limits
  - price set if type=limit
Response: { trade_id, status, order_id?, timestamp }
Errors: 401 (auth), 400 (validation), 403 (insufficient funds), 500 (execution)
```

### GET `/trades` — List Trades
```
Query: ?symbol=&status=&days=
Response: [ { trade_id, symbol, quantity, side, status, timestamp, result } ]
```

### GET `/trades/{trade_id}` — Trade Details
```
Response: { trade_id, symbol, quantity, side, status, order_id, fill_price, timestamp }
```

### POST `/trades/{trade_id}/cancel` — Cancel Trade
```
Validation: trade_id exists, status is "pending"
Response: { trade_id, status: "cancelled", timestamp }
Errors: 400 (not cancellable), 404 (trade not found)
```

## Error Handling Pattern

```python
from flask import jsonify

@trades_bp.post('/trades')
def place_trade():
    try:
        # Authenticate
        user = auth_service.verify_token(request.headers.get('Authorization'))
        
        # Validate
        data = request.get_json()
        symbol = validate_symbol(data.get('symbol'))
        quantity = validate_quantity(data.get('quantity'))
        
        # Execute
        result = trading_engine.execute_trade(symbol, quantity, ...)
        
        # Return
        return jsonify(result.to_dict()), 200
        
    except AuthError as e:
        return jsonify({"error": str(e)}), 401
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except InsufficientFundsError as e:
        return jsonify({"error": str(e)}), 403
    except TradingError as e:
        logger.exception(f"Trade execution failed: {e}")
        return jsonify({"error": "Internal server error"}), 500
```

## Testing Trade Endpoints

Every endpoint needs:
- Happy path test (successful trade)
- Auth failure test (missing/invalid token)
- Validation failure tests (bad symbol, quantity, etc.)
- Business logic failure tests (no funds, halted stock)
- Integration test with trading engine mock

```python
def test_place_trade_success(client, mock_trading_engine):
    response = client.post(
        '/trades',
        json={'symbol': 'AAPL', 'quantity': 10, 'side': 'buy'},
        headers={'Authorization': 'Bearer valid_token'}
    )
    assert response.status_code == 200
    assert response.json['status'] == 'executed'
```
