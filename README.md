# TradeWiser Bot

**AI-assisted automated trading bot with momentum strategy, expert watchlist, and options support.**

TradeWiser runs as a Windows Service, connects to Alpaca Markets for execution, monitors 72+ symbols (stocks + ATM options), filters every trade signal through a local or cloud LLM, and exposes a REST API for live control and monitoring.

---

## Features

- **Momentum strategy** — buys on upward price momentum, sells on downward; configurable window and thresholds
- **AI trade filter** — every BUY/SELL signal is evaluated by an LLM before execution; only high-confidence signals fire
- **Expert watchlist** — 24 symbols curated across commodities, REITs, value, growth, index ETFs, and crypto proxies
- **Options trading** — automatically generates ATM call + put symbols (OCC format) for every stock on the watchlist
- **Batched quotes** — all 72+ symbols fetched in 2 API calls per cycle (36× fewer requests than per-symbol polling)
- **P&L dashboard** — live account equity, unrealized/realized P&L per position
- **Position persistence** — syncs open positions from Alpaca on startup; no amnesia after restarts
- **Dynamic watchlist** — add/remove symbols at runtime via REST API without restarting
- **Audit log** — every trade attempt (manual or automated) recorded with timestamps and results
- **Windows Service** — auto-starts, survives reboots, configurable via `.env`

---

## AI Trade Advisor

Before every BUY or SELL, the bot asks an LLM:
> *"Given this price trend, recent news, and expert thesis — is this a good trade right now?"*

The LLM returns `{ action, confidence, reason }`. Only trades with **confidence ≥ 0.65** execute.
Decisions are cached per symbol for 90 seconds to avoid API spam.

### Provider priority

| Priority | Provider | Setup |
|----------|----------|-------|
| 1 | **Groq** (cloud, free tier) | Set `GROQ_API_KEY` in `.env` |
| 2 | **Ollama** (local, free forever) | Install Ollama + `ollama pull llama3.2` |

**Groq setup** — free, no credit card, 14,400 req/day:
1. Sign up at [console.groq.com](https://console.groq.com)
2. Create an API key
3. Add to `.env`: `GROQ_API_KEY=gsk_...`

**Ollama setup** — runs on this machine, works offline:
1. Download from [ollama.com/download/windows](https://ollama.com/download/windows)
2. Run `ollama pull llama3.2` (one-time ~2 GB download)
3. Leave `GROQ_API_KEY=` empty — bot auto-detects Ollama at `localhost:11434`

---

## Expert Watchlist

24 symbols pre-loaded at startup, inspired by industry experts and value/momentum investing principles:

| Category | Symbols |
|----------|---------|
| Commodities | GLD, SLV, GDX, XLE |
| REITs | O, VNQ |
| Crypto proxy | MSTR, IBIT |
| Value / Buffett | KO, OXY, AXP, BAC, CVX, AAPL |
| Growth / Tech | NVDA, MSFT, META, AMZN, GOOGL, TSLA, PLTR |
| Index ETFs | SPY, QQQ, IWM |

For each stock the bot automatically generates the nearest ATM call + put options expiring in ~2 weeks (OCC format), bringing the active watchlist to **72 symbols** by default.

---

## Quick Start (Development)

### Prerequisites
- Python 3.8+
- Alpaca account — [free paper trading](https://alpaca.markets/)
- (Optional) Groq account or Ollama installed for AI filtering

### Install
```bash
git clone https://github.com/YOUR_USERNAME/tradewiser-bot.git
cd tradewiser-bot/tradewiser_bot
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### Configure
```env
# .env
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # paper trading
GROQ_API_KEY=gsk_...                               # optional — Ollama fallback if omitted
POLL_INTERVAL=5
```

### Run
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Production Deployment (Windows Service)

### Build MSI
```cmd
cd tradewiser_bot
build_msi.bat
```

Requires Python 3.8+, pip, and [WiX Toolset v3](https://github.com/wixtoolset/wix3/releases) on `PATH`.

### Install
```cmd
msiexec /i tradewiser.msi
```

Installs to `C:\Program Files (x86)\TradeWiser\TradeWiser Bot\` and registers the `TradeWiserBot` Windows Service.

### Configure (installed)
Edit `C:\Program Files (x86)\TradeWiser\TradeWiser Bot\.env` then restart the service.

### Service management
```powershell
sc.exe start TradeWiserBot
sc.exe stop TradeWiserBot
sc.exe query TradeWiserBot
```

### Status dashboard
```powershell
.\status.ps1
```
Shows service state, API health, strategy signals, P&L, open positions, and recent trades.

---

## API Reference

All endpoints require `X-API-Key` header when `BOT_API_KEY` is set in `.env`.

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health check |

### Quotes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/quotes/{symbol}` | Live quote for any symbol |

### Trades

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/trades/execute` | Place a manual trade |
| GET | `/trades/current` | Open orders on Alpaca |
| GET | `/trades/pnl` | Account P&L + per-position breakdown |
| GET | `/trades/audit` | Full audit log (last 100 trades) |
| GET | `/trades/audit/{id}` | Single audit entry |
| GET | `/trades/strategy/status` | Live momentum data for all symbols |
| GET | `/trades/ai-status` | AI provider, model, and cached decisions |
| GET | `/trades/options/chain/{symbol}` | Options chain for a symbol |

#### Manual trade request body
```json
{
  "symbol": "AAPL",
  "quantity": 1,
  "side": "BUY",
  "order_type": "MKT",
  "enforce": "GTC",
  "dry_run": true
}
```
`dry_run: true` validates the order without submitting it.

### Watchlist

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/watchlist/active` | Current trading symbols (stocks + options) |
| POST | `/watchlist/active` | Add symbol (optionally with ATM options) |
| DELETE | `/watchlist/active/{symbol}` | Remove symbol and its options |
| GET | `/watchlist/experts` | Browse expert-curated symbols by category |
| GET | `/watchlist/scan` | Live news-sentiment scan (top N symbols) |
| GET | `/watchlist/options/{symbol}` | Generate ATM call + put OCC symbols |

#### Add symbol with options
```json
POST /watchlist/active
{ "symbol": "NVDA", "include_options": true, "weeks_out": 2 }
```

---

## Trading Strategy

### Momentum algorithm
1. Collect 5 price points over 25 seconds (configurable via `MOMENTUM_WINDOW`)
2. Calculate rate of change: `(current - oldest) / oldest`
3. **Buy signal**: momentum > +0.2% and no existing position
4. **AI filter**: ask Groq/Ollama — approve, override, or hold
5. Execute only if AI confidence ≥ 0.65
6. **Sell signal**: momentum < -0.2% and position held
7. Same AI filter before sell execution

### Parameters (in `trading_engine.py`)
```python
MOMENTUM_WINDOW        = 5      # price data points
MOMENTUM_THRESHOLD_BUY  = 0.002  # +0.2%
MOMENTUM_THRESHOLD_SELL = -0.002 # -0.2%
TRADE_QUANTITY         = 1      # shares per trade
```

---

## Project Structure

```
tradewiser_bot/
├── app/
│   ├── main.py                    # FastAPI app + lifespan (populates watchlist, starts trading loop)
│   ├── core/
│   │   ├── config.py              # Environment settings (Alpaca, Groq, poll interval)
│   │   ├── auth.py                # API key authentication
│   │   └── logger.py              # Structured logging
│   ├── routes/
│   │   ├── health.py              # /health
│   │   ├── quotes.py              # /quotes
│   │   ├── trades.py              # /trades (execute, pnl, audit, strategy, ai-status)
│   │   └── watchlist.py           # /watchlist (active, experts, scan, options)
│   └── services/
│       ├── trading_engine.py      # MomentumStrategy class + async trading loop
│       ├── ai_advisor.py          # LLM trade filter (Groq → Ollama fallback)
│       ├── watchlist_manager.py   # Expert picks, news sentiment, ATM option generator
│       ├── webull_client.py       # Alpaca API client (quotes, orders, positions, P&L)
│       └── utils.py               # SQLite audit log
├── windows_service.py             # Windows Service wrapper (PyWin32)
├── build_msi.bat                  # Build EXE + MSI
├── tradewiser.wxs                 # WiX installer config
├── status.ps1                     # Live status dashboard
├── requirements.txt
└── .env                           # Local config (not committed)
```

---

## Environment Variables

```env
# Required
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # or https://api.alpaca.markets for live

# AI advisor (optional — Ollama used if omitted)
GROQ_API_KEY=gsk_...

# API authentication (optional — leave empty to disable)
BOT_API_KEY=your_secret_key

# Trading cadence
POLL_INTERVAL=5   # seconds between price checks
```

---

## Security

- **Paper trading first** — always validate on paper before switching to `https://api.alpaca.markets`
- **API key protection** — set `BOT_API_KEY` to protect REST endpoints from unauthorized access
- **Credential isolation** — `.env` is never committed; the service reads it from the install directory
- **Least privilege** — the service account only needs network access and read/write to its install directory

---

## Disclaimer

This software is for educational and research purposes only. Trading involves substantial risk of loss. Past performance does not guarantee future results. Always consult a qualified financial advisor before making investment decisions. The authors are not responsible for any financial losses incurred through use of this software.
