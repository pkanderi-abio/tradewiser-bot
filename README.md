# TradeWiser Bot

TradeWiser is a lightweight FastAPI trading bot scaffold that integrates with **Alpaca** (much more reliable than Webull). It includes a background quote polling loop, quote endpoints, order execution support, and trade audit logging.

## Features

- FastAPI HTTP API with health, quote, and trade routes
- **Alpaca** trading integration with paper trading support
- Alpaca quote retrieval with yfinance fallback
- Background trading loop on FastAPI startup
- Order placement endpoint with dry-run validation
- Current order and order history endpoints
- Trade audit logging with `GET /trades/audit` and `GET /trades/audit/{id}`

## Project Structure

- `app/main.py` — FastAPI application entry point
- `app/core/config.py` — application settings and `.env` loading
- `app/core/logger.py` — logging configuration
- `app/services/webull_client.py` — Alpaca API wrapper (legacy filename, now uses Alpaca)
- `app/services/trading_engine.py` — polling loop and order strategy placeholder
- `app/routes/` — REST endpoints for health, quotes, and trades
- `app/services/utils.py` — trade audit log storage utilities

## Requirements

- Python 3.11+ recommended
- `alpaca-py` package for brokerage integration
- `yfinance` package for quote data fallback

## Installation

```bash
cd tradewiser_bot
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

## Configuration

### Get Alpaca API Keys (FREE)

1. Go to [https://alpaca.markets/](https://alpaca.markets/) and create a free account
2. Navigate to your dashboard and generate API keys
3. Choose "Paper Trading" for risk-free testing (unlimited virtual money)

Create a `.env` file in the project root with the following values:

```env
# Alpaca API credentials (recommended - much more reliable than Webull)
ALPACA_API_KEY=your-alpaca-api-key-here
ALPACA_SECRET_KEY=your-alpaca-secret-key-here
# Use https://paper-api.alpaca.markets for paper trading (free)
# Use https://api.alpaca.markets for live trading (requires funded account)
ALPACA_BASE_URL=https://paper-api.alpaca.markets

POLL_INTERVAL=10
```

> **Note**: Alpaca offers FREE paper trading with unlimited virtual funds. Start there before going live!

## Trading Strategy

The bot implements a **Momentum Trading Strategy** that automatically buys and sells based on price momentum:

### Momentum Strategy Details

- **Window**: Tracks last 5 price points for momentum calculation
- **Buy Signal**: When price increases by >0.5% over the window
- **Sell Signal**: When price decreases by >0.5% over the window
- **Trade Size**: 1 share per trade (configurable)
- **Watchlist**: SPY, QQQ, AAPL (configurable)

### Strategy Monitoring

- `GET /trades/strategy/status` - View current momentum, positions, and signals
- Real-time logging shows momentum calculations and trade decisions
- Audit trail tracks all trades and strategy decisions

### How It Works

1. **Price Tracking**: Collects price data every 10 seconds
2. **Momentum Calculation**: `(current_price - oldest_price) / oldest_price`
3. **Signal Generation**: Buy on positive momentum, sell on negative momentum
4. **Risk Management**: Avoids overtrading with price change thresholds
5. **Position Tracking**: Maintains current holdings and trade history

## API Endpoints

### Health

- `GET /health/`
  - Returns service status.

### Quotes

- `GET /quotes/`
  - Returns the configured watchlist.
- `GET /quotes/{symbol}`
  - Fetches the latest quote for a symbol via Alpaca (with yfinance fallback).

### Trades

- `GET /trades/status`
  - Returns trade endpoint status.
- `POST /trades/execute`
  - Submit a trade order via Alpaca.
  - Supports `dry_run: true` for validation without execution.
- `GET /trades/current`
  - Fetches current open orders from Alpaca.
- `GET /trades/history`
  - Fetches recent order history from Alpaca.
- `GET /trades/audit`
  - Returns audit log entries for trade requests.
- `GET /trades/strategy/status`
  - View momentum strategy status, current signals, and positions
  - Returns a single audit record by ID.

### Example Trade Request

```json
{
  "symbol": "AAPL",
  "quantity": 1,
  "side": "BUY",
  "order_type": "MKT",
  "dry_run": true
}
```

## Notes

- The trading loop currently uses a fixed watchlist of `SPY`, `QQQ`, and `AAPL`.
- The strategy is a placeholder; customize `app/services/trading_engine.py` for real trade logic.
- If Webull authentication fails (due to API changes), the bot automatically falls back to yfinance for quote data.
- Protect credentials and never commit real Webull login details.

## Troubleshooting

- If the app fails to start due to missing settings, verify `.env` exists in the repository root.
- If Webull login fails, confirm username/password and review logs.

## License

This project is provided as-is for experimentation and development.
