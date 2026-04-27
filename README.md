# TradeWiser Bot 🤖📈

**Automated Trading Bot with Momentum Strategy**

TradeWiser is a production-ready automated trading bot that uses momentum-based strategies to trade stocks and options through the Alpaca API. Features include real-time market monitoring, automatic trade execution, comprehensive audit logging, and Windows MSI packaging for enterprise deployment.

## ✨ Features

### Core Trading Features
- **Momentum Strategy**: Automated buying/selling based on configurable price momentum thresholds
- **Multi-Asset Support**: Stocks and options trading with real-time quotes
- **Alpaca Integration**: Live and paper trading support with reliable API connectivity
- **Background Processing**: Asynchronous trading loop that runs 24/7
- **Risk Management**: Configurable thresholds and position limits

### API & Monitoring
- **REST API**: FastAPI-based endpoints for health checks, strategy status, and manual trading
- **Real-time Monitoring**: Live strategy status with momentum calculations
- **Audit Logging**: Complete trade history with timestamps and execution details
- **Health Checks**: System monitoring and connectivity validation

### Production Deployment
- **Windows MSI Packaging**: Enterprise-ready installer for Windows Server
- **Windows Service**: Auto-start service with proper permissions
- **Production Configuration**: Environment-based settings for live trading
- **Deployment Automation**: PowerShell scripts for streamlined server deployment

## 🚀 Quick Start (Development)

### Prerequisites
- Python 3.8+
- Alpaca account ([Free Paper Trading](https://alpaca.markets/))

### Installation
```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/tradewiser-bot.git
cd tradewiser-bot/tradewiser_bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration
```bash
# Copy sample configuration
cp sample.env .env

# Edit .env with your Alpaca credentials
# ALPACA_API_KEY=your_api_key_here
# ALPACA_SECRET_KEY=your_secret_key_here
# ALPACA_BASE_URL=https://paper-api.alpaca.markets  # Paper trading
```

### Run the Bot
```bash
# Start the trading bot
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# API will be available at: http://localhost:8000
```

### Test the API
```bash
# Health check
curl http://localhost:8000/health

# Strategy status (shows momentum for all monitored symbols)
curl http://localhost:8000/trades/strategy/status

# View trade audit log
curl http://localhost:8000/trades/audit
```

## 🏗️ Production Deployment (Windows Server)

### Build MSI Package
```cmd
# On Windows machine with Python + WiX Toolset
cd tradewiser_bot
build_msi.bat
```

### Deploy to Windows Server
```powershell
# Install MSI
msiexec /i tradewiser.msi

# Configure with production credentials
.\deploy.ps1 -AlpacaApiKey "YOUR_LIVE_API_KEY" -AlpacaSecretKey "YOUR_LIVE_SECRET" -UseLiveTrading
```

See [README_Windows_Deployment.md](README_Windows_Deployment.md) for detailed deployment instructions.

## 📊 Trading Strategy

### Momentum Algorithm
- **Monitors**: 9 symbols (3 stocks + 6 options contracts)
- **Buy Threshold**: +0.2% price momentum
- **Sell Threshold**: -0.2% price momentum
- **Window**: 5-period moving average
- **Poll Interval**: 5 seconds

### Monitored Symbols
**Stocks**: SPY, QQQ, AAPL
**Options**: Near-the-money calls and puts for each stock

### Auto-Trading Flow
1. Collect 5 price points over 25 seconds
2. Calculate momentum percentage change
3. Execute buy/sell orders when thresholds crossed
4. Log all trades to audit trail

## 🔧 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check |
| GET | `/trades/strategy/status` | Real-time strategy status |
| GET | `/trades/audit` | Complete trade history |
| POST | `/trades/execute` | Manual trade execution |
| GET | `/quotes/{symbol}` | Get current quote |

### Strategy Status Response
```json
{
  "status": "ok",
  "strategy": "momentum",
  "parameters": {
    "window": 5,
    "buy_threshold": 0.002,
    "sell_threshold": -0.002,
    "trade_quantity": 1
  },
  "momentum_data": {
    "SPY": {
      "current_price": 510.25,
      "momentum": 0.0015,
      "momentum_percent": "0.15%",
      "should_buy": false,
      "should_sell": false
    }
  }
}
```

## 🏛️ Project Structure

```
tradewiser_bot/
├── app/
│   ├── main.py                 # FastAPI application
│   ├── core/
│   │   ├── config.py          # Environment configuration
│   │   ├── logger.py          # Logging setup
│   │   └── scheduler.py       # Background task management
│   ├── routes/
│   │   ├── health.py          # Health check endpoints
│   │   ├── quotes.py          # Quote retrieval
│   │   └── trades.py          # Trading endpoints
│   └── services/
│       ├── trading_engine.py  # Momentum strategy logic
│       ├── webull_client.py   # Alpaca API client
│       └── utils.py           # Audit logging utilities
├── build_msi.bat              # Windows MSI build script
├── deploy.ps1                 # Production deployment script
├── tradewiser.wxs             # WiX installer configuration
├── windows_service.py         # Windows service wrapper
├── requirements.txt           # Python dependencies
├── sample.env                # Configuration template
└── README_Windows_Deployment.md # Detailed deployment guide
```

## ⚙️ Configuration

### Environment Variables
```env
# Alpaca API Credentials
ALPACA_API_KEY=your_api_key
ALPACA_SECRET_KEY=your_secret_key
ALPACA_BASE_URL=https://api.alpaca.markets  # Live trading

# Trading Parameters
POLL_INTERVAL=5  # Seconds between checks

# Legacy (not used)
WEBULL_EMAIL=
WEBULL_PASSWORD=
WEBULL_DEVICE_NAME=TradeWiserBot
```

### Alpaca Account Setup
1. Visit [alpaca.markets](https://alpaca.markets/)
2. Create free account
3. Generate API keys in dashboard
4. Start with paper trading for testing

## 🔒 Security & Best Practices

- **Paper Trading First**: Always test with paper trading before live deployment
- **Secure Credentials**: Store API keys securely, never commit to version control
- **Risk Management**: Set appropriate position sizes and stop losses
- **Monitoring**: Regularly check trade audit logs and system health
- **Updates**: Keep dependencies updated and monitor Alpaca API changes

## 📈 Performance & Reliability

- **High-Frequency Monitoring**: 5-second poll intervals for responsive trading
- **Error Handling**: Comprehensive error handling with fallbacks
- **Connection Resilience**: Automatic reconnection and retry logic
- **Audit Trail**: Complete logging of all trading activities
- **Health Monitoring**: Built-in health checks for system status

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## ⚠️ Disclaimer

This software is for educational and informational purposes only. Trading involves substantial risk of loss and is not suitable for every investor. Past performance does not guarantee future results. Please consult with a qualified financial advisor before making investment decisions.

---

**Built with ❤️ for automated trading**

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
