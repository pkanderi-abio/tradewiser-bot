# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Overview

TradeWiser is an AI-assisted autonomous trading bot that runs as a Windows Service. It monitors 24 curated stocks + dynamically generated ATM call options, filters every trade signal through an LLM (Groq, OpenAI, Anthropic, or local Ollama), and exposes a REST API for live control and monitoring via FastAPI.

## Repository Layout

```
tradewiser_bot/
├── app/                         # Application code
│   ├── core/
│   │   ├── auth.py              # X-API-Key middleware
│   │   ├── config.py            # Pydantic Settings (loads .env)
│   │   └── logger.py            # UTF-8 logger setup
│   ├── routes/                  # FastAPI routers
│   │   ├── health.py            # /health (unauthenticated)
│   │   ├── quotes.py            # /quotes/*  (X-API-Key required)
│   │   ├── trades.py            # /trades/*  (X-API-Key required)
│   │   └── watchlist.py         # /watchlist/* (X-API-Key required)
│   ├── services/                # Business logic
│   │   ├── alpaca_client.py     # Alpaca API wrapper (orders + batched quotes)
│   │   ├── ai_advisor.py        # Legacy AIAdvisor (Groq/Ollama) — WIRED IN
│   │   ├── trading_engine.py    # DailyRSIStrategy + start_trading_loop()
│   │   ├── watchlist_manager.py # EXPERT_PICKS + ATM option symbol generator
│   │   ├── utils.py             # SQLite audit log
│   │   ├── llm_service.py       # Multi-provider LLM (WIP, not wired in)
│   │   ├── enhanced_ai_advisor.py  # Multi-factor advisor (WIP, not wired in)
│   │   ├── sentiment_analyzer.py   # WIP
│   │   ├── news_analyzer.py        # WIP
│   │   ├── market_intelligence.py  # WIP
│   │   └── strategy_agents.py      # WIP
│   └── main.py                  # FastAPI app + lifespan (starts trading loop)
├── tests/                       # pytest suite — all tests live here
├── deploy.ps1                   # Install service + venv to C:\Program Files (x86)\TradeWiser\
├── sync-to-installed.ps1        # Quick sync source -> installed dir + restart service
├── status.ps1                   # Live P&L / signal dashboard (auto-reads BOT_API_KEY)
├── windows_service.py           # pywin32 service wrapper for uvicorn
├── build-msi.ps1                # Build MSI via WiX Toolset
├── tradewiser.wxs               # WiX manifest
├── requirements.txt             # Runtime deps
├── requirements-test.txt        # Test-only deps
└── sample.env                   # Template — copy to .env, fill in keys
```

## Commands

### Local development
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Copy sample.env to .env, fill in ALPACA_*, BOT_API_KEY, and one LLM key
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# Swagger: http://localhost:8000/docs
```

### Tests
```powershell
pip install -r requirements-test.txt
pytest tests\ -v
pytest tests\ --cov=app --cov-report=term-missing
```

### Windows Service deployment
```powershell
# First-time install (creates venv, registers service, copies files):
.\deploy.ps1

# After code changes — quick sync + restart (no MSI rebuild):
.\sync-to-installed.ps1

# Build a redistributable MSI (requires WiX v3 or v4 on PATH):
.\build-msi.ps1
msiexec /i tradewiser.msi

# Service control:
Start-Service TradeWiserBot
Stop-Service TradeWiserBot
Restart-Service TradeWiserBot
.\status.ps1                  # Live dashboard
```

### Auto-deploy via Claude Code hook

A PostToolUse hook in `.claude/settings.local.json` runs `sync-to-installed.ps1` automatically after Claude edits any file under `app/` or modifies `windows_service.py`. The hook syncs the changed file to the install directory and restarts `TradeWiserBot`.

**To disable temporarily** (e.g. during market hours): comment out the hook entry, or use `/hooks` to disable it in the UI.

## Architecture

### Request flow
```
REST API (FastAPI) → Routes → Services → AlpacaClient (execution/quotes)
                                       ↘ AIAdvisor (signal filtering)
Background loop ───→ start_trading_loop() ──→ WatchlistManager
```

### Key modules

**`app/core/config.py`** — Pydantic `Settings`. All trading parameters, LLM provider config, and API keys loaded from `.env`. Single source of truth — nothing hardcoded in services. `find_env_file()` checks the install dir first, then the repo root, then CWD.

**`app/services/trading_engine.py`** — `DailyRSIStrategy` class + `start_trading_loop()`. Computes RSI(14), SMA(50), HV rank from 1-year daily OHLCV; generates buy/sell signals; gates through `ai_advisor.should_trade()`; places market orders on ATM options. Manages trailing stops and expiry-based exits. `momentum_strategy` is kept as a backward-compat alias for `rsi_strategy`.

**`app/services/alpaca_client.py`** — `AlpacaClient` wraps Alpaca API calls. Use `get_batch_quotes(symbols)` for multi-symbol fetches — never loop single-symbol calls.

**`app/services/ai_advisor.py`** — Legacy `AIAdvisor` (Groq/Ollama). **This is the one wired into the trading engine.** Imported at `trading_engine.py:12`.

**`app/services/watchlist_manager.py`** — `EXPERT_PICKS` dict of 24 stocks across 6 categories. `get_atm_option_symbols()` generates OCC-format option symbols (4-week expiry, ATM strike).

**`app/services/utils.py`** — SQLite audit log (`record_audit_entry`, `get_audit_log`).

**Enhanced AI modules** (`llm_service.py`, `enhanced_ai_advisor.py`, `sentiment_analyzer.py`, `news_analyzer.py`, `market_intelligence.py`, `strategy_agents.py`) — WIP, not yet wired into `trading_engine.py`. Have test coverage in `tests/test_llm_services.py`.

**`tests/conftest.py`** — Shared fixtures: `client` (unauthenticated TestClient), `authed_client` (with `X-API-Key`), `mock_alpaca` (patched AlpacaClient).

### Options strategy

Trades are placed on **ATM call options**, not the underlying stock. A buy signal on `AAPL` calls `get_atm_option_symbols("AAPL")` to get the OCC symbol (e.g. `O:AAPL250117C00185000`), then orders that. Sell logic tracks option P&L, not stock price.

### AI signal filtering

Every buy signal passes through `ai_advisor.should_trade(symbol, rsi, price, sma)` before execution. The AI returns: approve / override / hold. Confidence must meet `AI_MIN_CONFIDENCE` (default 0.65).

## Environment variables

Required in `.env` (full template in `sample.env`):

```env
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets   # paper default

# One LLM provider (Groq recommended — free tier, 14,400 req/day)
GROQ_API_KEY=gsk_...
# OPENAI_API_KEY=sk-proj-...
# ANTHROPIC_API_KEY=sk-ant-...
# OLLAMA_ENABLED=true

BOT_API_KEY=<strong-random-value>   # required; service refuses to start if missing or "dev-key-disabled"
```

Key trading parameters (defaults in `config.py`):

| Setting | Default | Purpose |
|---|---|---|
| `AI_MIN_CONFIDENCE` | 0.65 | LLM confidence threshold |
| `TRADING_MAX_POSITION_SIZE` | 1000 | Dollars per position |
| `SCHEDULER_ENABLED` | true | Autonomous loop on/off |
| `SCHEDULER_INTERVAL_SECONDS` | 60 | Loop cadence |
| `POLL_INTERVAL` | 5 | Price-check interval |

## Conventions

- **Authentication**: `X-API-Key` required on `/trades/*`, `/quotes/*`, `/watchlist/*`. Test fixtures use `authed_client` for these.
- **Async everywhere**: `start_trading_loop()` is launched from FastAPI lifespan via `asyncio.create_task`.
- **LLM caching**: `LLMService` caches by symbol+prompt hash; `AI_DECISION_CACHE_TTL=3600` prevents re-querying the same signal within an hour.
- **Paper trading default**: `ALPACA_BASE_URL` defaults to paper. Switching to live requires an explicit env change.
- **Windows UTF-8**: `app/core/logger.py` forces UTF-8 on stdout (`sys.stdout.reconfigure(encoding='utf-8')`). Do not remove.
- **PowerShell scripts**: Save as UTF-8 *with BOM* — PowerShell 5.1 reads BOM-less files as Windows-1252, which corrupts non-ASCII content.
- **Service host**: Service runs as `LocalSystem` and uses `pythonservice.exe` from the venv. The Python 3.14 DLL directory must be on the *machine-level* PATH so LocalSystem can find `python314.dll`. `deploy.ps1` handles this.

## Trading strategy parameters

Defined as constants in `app/services/trading_engine.py`:

| Parameter | Value | Meaning |
|---|---|---|
| `RSI_BUY_THRESHOLD` | 35 | Buy when RSI < 35 (oversold) |
| `RSI_SELL_THRESHOLD` | 70 | Sell when RSI > 70 (overbought) |
| `PROFIT_TARGET` | 0.60 | Exit option at +60% gain |
| `STOP_LOSS` | 0.30 | Exit option at -30% loss |
| `MAX_POSITIONS` | 5 | Max concurrent option trades |
| `IV_RANK_MAX` | 50 | Skip if HV rank > 50% |
| `EARNINGS_DAYS_MIN` | 7 | Skip if earnings within 7 days |
| `TRAILING_STOP_ACTIVATION` | 0.20 | Arm trailing stop at +20% |
| `OPTION_WEEKS_OUT` | 4 | Buy ~4-week expiry options |
| `DAYS_BEFORE_EXPIRY` | 3 | Close 3 days before expiry |
