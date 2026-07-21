# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
│   │   ├── ai_advisor.py        # Hardened AIAdvisor + stage-2 ensemble — WIRED IN
│   │   ├── ai_guardrails.py     # AIDecision schema + sanitizer + CircuitBreaker
│   │   ├── risk_gate.py         # Pre-trade portfolio risk gate — WIRED IN
│   │   ├── regime.py            # Market regime gate (VIX/trend) — WIRED IN
│   │   ├── market_data.py       # VIX + SPY/QQQ trend snapshot (yfinance, cached)
│   │   ├── news_feed.py         # Alpaca news → yfinance fallback, sanitized
│   │   ├── sentiment_feed.py    # StockTwits sentiment (best-effort, no API key)
│   │   ├── pnl.py               # FIFO realized P&L from trade_audit
│   │   ├── trading_engine.py    # DailyRSIStrategy + start_trading_loop()
│   │   ├── watchlist_manager.py # EXPERT_PICKS + ATM option symbol generator
│   │   ├── utils.py             # SQLite audit (trade_audit + ai_decisions + risk_events + account_snapshots)
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
                                       ↘ AIAdvisor (signal filtering, optional stage-2 confirm)
                                       ↘ RiskGate    (portfolio safety)
                                       ↘ RegimeGate  (macro skip)
Background loop ───→ start_trading_loop() ──→ WatchlistManager
                                          ↘  MarketData / NewsFeed / SentimentFeed
```

The trading-engine BUY path runs: **strategy signal → RegimeGate.classify() (once per pass) → AIAdvisor.decide() (per symbol, optionally stage-2 confirmed) → RiskGate.evaluate() → execute_buy()**. Every gate persists its evaluation to SQLite for post-hoc review.

### Key modules

**`app/core/config.py`** — Pydantic `Settings`. All trading parameters, LLM provider config, and API keys loaded from `.env`. Single source of truth — nothing hardcoded in services. `find_env_file()` checks the install dir first, then the repo root, then CWD.

**`app/services/trading_engine.py`** — `DailyRSIStrategy` class + `start_trading_loop()`. Computes RSI(14), SMA(50), HV rank from 1-year daily OHLCV; generates buy/sell signals; gates through `ai_advisor.should_trade()`; places market orders on ATM options. Manages trailing stops and expiry-based exits. `momentum_strategy` is kept as a backward-compat alias for `rsi_strategy`.

**`app/services/alpaca_client.py`** — `AlpacaClient` wraps Alpaca API calls. Use `get_batch_quotes(symbols)` for multi-symbol fetches — never loop single-symbol calls. Auth failures latch `_auth_failed` for `ALPACA_AUTH_RETRY_COOLDOWN_SECONDS` (default 60) only — after the cooldown the client retries from scratch and can recover from transient outages without a service restart (the May-2026 27-day silent-down incident motivated this). `broker_snapshot()` exposes auth state + last error + cooldown progress for `GET /health/broker`.

**`app/services/ai_advisor.py`** — Hardened `AIAdvisor` (Groq/Ollama). **This is the one wired into the trading engine.** Imported at `trading_engine.py:12`. Every `decide()` goes through: kill-switch check → cache → circuit-breaker check → LLM call with timeout + retries → pydantic schema validation → fail-closed on error → persist to `ai_decisions` table.

**`app/services/ai_guardrails.py`** — `AIDecision` (pydantic schema the LLM response must conform to), `sanitize_headlines()` (drops prompt-injection patterns from untrusted news input), and `CircuitBreaker` (opens after N consecutive failures, half-open after cooldown).

**`app/services/risk_gate.py`** — `RiskGate.evaluate(symbol, action, notional)` runs AFTER AI approval and BEFORE order placement. Checks concentration cap, daily-loss floor (`day_pl + unrealized_pl`), and drawdown vs rolling peak equity (`account_snapshots` table). Every evaluation is persisted to `risk_events`. **Fail-open** on broker outage — a transient quotes failure should not halt all trading. Knows how to extract the underlying from OCC option symbols (`AAPL250117C00185000` → `AAPL`).

**`app/services/watchlist_manager.py`** — `EXPERT_PICKS` dict of 24 stocks across 6 categories. `get_atm_option_symbols()` generates OCC-format option symbols (4-week expiry, ATM strike).

**`app/services/utils.py`** — SQLite-backed audit. Four tables in `settings.AI_AUDIT_DB_PATH`: `trade_audit` (legacy API: `record_audit_entry`/`get_audit_log`/`get_audit_entry`), `ai_decisions` (every LLM call with prompt hash, latency, attempts, token usage, circuit state, outcome), `risk_events` (every gate evaluation with breaches + snapshot), `account_snapshots` (equity readings for the drawdown peak). Tests use `:memory:` via conftest and call `truncate_tables_for_tests(...)` to isolate state.

**Enhanced AI modules** (`llm_service.py`, `enhanced_ai_advisor.py`, `sentiment_analyzer.py`, `news_analyzer.py`, `market_intelligence.py`, `strategy_agents.py`) — WIP, not yet wired into `trading_engine.py`. Have test coverage in `tests/test_llm_services.py`.

**`tests/conftest.py`** — Shared fixtures: `client` (unauthenticated TestClient), `authed_client` (with `X-API-Key`), `mock_alpaca` (patched AlpacaClient).

### Options strategy

Trades are placed on **ATM call options**, not the underlying stock. A buy signal on `AAPL` calls `get_atm_option_symbols("AAPL")` to get the OCC symbol (e.g. `O:AAPL250117C00185000`), then orders that. Sell logic tracks option P&L, not stock price.

### AI signal filtering

Every BUY/SELL passes through `ai_advisor.decide(...)` before execution. The advisor returns `{action, confidence, reason}`. Confidence must meet `AI_MIN_CONFIDENCE` (default 0.65), otherwise the signal is dropped.

### AI reliability controls (production-grade)

The advisor is **fail-closed**: any LLM error, schema violation, timeout, or open circuit returns `HOLD` with confidence 0 — autonomous trading never silently bypasses the AI gate. Controlled via these env vars (all in `app/core/config.py`):

| Setting | Default | Purpose |
|---|---|---|
| `AI_KILL_SWITCH` | false | Emergency stop — forces every decision to HOLD without a restart |
| `AI_FAIL_CLOSED` | true | LLM error → HOLD. Set false only for non-live testing |
| `AI_REQUEST_TIMEOUT_SECONDS` | 15 | Per-call wall-clock budget |
| `AI_MAX_RETRIES` | 2 | Retries on transient failures (timeout, 5xx, network) |
| `AI_RETRY_BACKOFF_SECONDS` | 0.75 | Initial backoff; doubles each attempt |
| `AI_CIRCUIT_BREAKER_THRESHOLD` | 5 | Consecutive failures before circuit opens |
| `AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS` | 120 | Cooldown before a probe call is allowed |
| `AI_AUDIT_DB_PATH` | `tradewiser_audit.db` | SQLite path for trade + AI audit. `:memory:` in tests |
| `AI_MAX_NEWS_HEADLINES` | 6 | News input cap (prompt-injection budget + cost) |
| `AI_MAX_HEADLINE_CHARS` | 160 | Per-headline truncation after sanitization |

Headlines are sanitized before entering the prompt — any line containing an injection pattern (`ignore previous instructions`, `<system>` markers, etc.) is dropped, not redacted. The advisor's live state (circuit, kill switch, recent error rate, last decisions, token spend) is exposed at `GET /trades/ai-status`.

### Pre-trade risk gate

`risk_gate.evaluate()` runs after AI approval and can block a trade based on portfolio state the AI cannot see. **The AI decides if a signal is technically worth taking; the risk gate decides if the portfolio can afford to take it.** Knobs:

| Setting | Default | Purpose |
|---|---|---|
| `RISK_GATE_ENABLED` | true | Master switch |
| `RISK_MAX_SYMBOL_CONCENTRATION_PCT` | 25.0 | Reject BUY if proposed position would push one underlying above this % of equity. Options are matched to their underlying ticker |
| `RISK_MAX_DAILY_LOSS_DOLLARS` | 500.0 | Halt new BUYs if `day_pl + unrealized_pl` < this loss |
| `RISK_MAX_DRAWDOWN_PCT` | 15.0 | Halt new BUYs if equity is this % below the rolling peak |
| `RISK_PEAK_EQUITY_WINDOW_DAYS` | 30 | Window for the rolling peak — calculated from `account_snapshots` |
| `RISK_HALT_BLOCKS_SELLS` | false | Default: always allow exits even when limits are breached. Set true to also block SELLs |

The gate **fails-open on broker outage** (logs a warning, allows the trade) — a transient Alpaca failure should not halt all trading. Concentration is skipped on SELL by definition (exits reduce concentration). The trading engine estimates option notional as `stock_price × 0.05 × 100 × qty` (rough ATM 4-week premium) before evaluating — see `_estimate_option_notional` in `trading_engine.py`. Live posture is exposed at `GET /trades/risk-status`.

### Cost telemetry

`ai_decisions.prompt_tokens` / `completion_tokens` are captured from `response.usage` on every successful LLM call (Groq + OpenAI return this; Ollama may not). `ai_token_stats(window_minutes)` rolls them up by symbol; `GET /trades/ai-status` exposes a `tokens` block with totals + top symbols by spend.

### Multi-model ensemble (stage-2 confirm)

After stage-1 (Groq or Ollama) succeeds with a BUY/SELL, the advisor optionally routes "borderline" decisions through a smarter model (Anthropic Claude or OpenAI). Routing rules in `ai_advisor._should_confirm`:

- Stage-2 only fires when stage-1 confidence is in `[ENSEMBLE_CONFIRM_BAND_LOW, ENSEMBLE_CONFIRM_BAND_HIGH)` (default 0.65–0.85). Above the high band, stage-1 is trusted alone (don't pay for what we already know). Below the low band the trade was already going to be filtered.
- `ENSEMBLE_PROVIDER=auto` (default) picks Anthropic if `ANTHROPIC_API_KEY` is set, else OpenAI if `OPENAI_API_KEY` is set, else stage-2 is disabled.
- If stage-2 fails (no key, timeout, schema error), stage-1's verdict stands. Stage-2 only ever *upgrades* the decision; it cannot *break* it.
- Both rows are persisted to `ai_decisions` with `stage='stage1'` and `stage='stage2'` so you can query "every time stage-2 disagreed with stage-1" for offline evaluation.

Knobs: `ENSEMBLE_ENABLED`, `ENSEMBLE_CONFIRM_BAND_LOW`, `ENSEMBLE_CONFIRM_BAND_HIGH`, `ENSEMBLE_PROVIDER`, `ENSEMBLE_ANTHROPIC_MODEL`, `ENSEMBLE_OPENAI_MODEL`. Live status at `GET /trades/ai-status` under the `ensemble` block.

### Market data and regime gate

`market_data.market_data_feed.snapshot()` returns a cached (5 min TTL) `MarketSnapshot` with VIX + day-over-day change, SPY and QQQ trend (`uptrend` / `downtrend` / `chop` based on 50/200-day SMAs), and SPY distance from the 50-day. All data is free via yfinance — no API key.

`regime.RegimeGate.classify()` consumes the snapshot and returns `{regime, allow_new_buys, reason, vix, spy_trend}` where `regime` ∈ {`calm_uptrend`, `chop`, `elevated_vol`, `downtrend`, `panic`, `disabled`, `unknown`}. The trading engine calls this once per BUY-evaluation pass and drops the entire BUY phase if `allow_new_buys` is false. Per-symbol AI + risk still run for SELLs. The gate **fails-open** if market data is unavailable.

Knobs: `REGIME_GATE_ENABLED`, `REGIME_VIX_PANIC_LEVEL` (default 35), `REGIME_VIX_ELEVATED_LEVEL` (default 25, informational), `REGIME_BLOCK_ON_DOWNTREND`, `REGIME_BLOCK_ON_PANIC_VIX`. Live state at `GET /trades/market-regime`.

### News and social sentiment feeds

`news_feed.NewsFeed.headlines(symbol)` prefers Alpaca's news API (real-time, uses existing Alpaca credentials, no extra key) and falls back to yfinance if Alpaca news fails. Headlines are sanitized through `ai_guardrails.sanitize_headlines` (drops prompt-injection patterns + control chars). `ai_advisor` calls this instead of fetching news itself.

`sentiment_feed.SentimentFeed.sentiment(symbol)` queries the public StockTwits stream endpoint (no API key, rate-limited). On 429 the feed latches off for the process — retail sentiment is a confirmation signal, not a primary one, so a rate limit isn't worth retrying. Returns `None` when unavailable and the AI prompt simply gets a "no social signal" line.

### Broker reliability

Alpaca auth uses a cooldown-based retry rather than a one-way latch. The
previous behavior — `_auth_failed = True` forever — caused a real 27-day
silent outage in May 2026: a single startup blip latched the flag and the
service kept running with `get_account_pnl()` returning `None`, `/quotes/*`
silently falling back to yfinance, and no trades placed. The audit log was
empty (in-memory only at the time) so nothing showed it was broken.

Now: after a failure, `_ensure_authenticated()` short-circuits for
`ALPACA_AUTH_RETRY_COOLDOWN_SECONDS` (default 60) to avoid hammering Alpaca,
then automatically retries the next call. `broker_snapshot()` exposes
authenticated state, latest error, consecutive-failure count, last success
time, and seconds-until-retry. The `/health/broker` endpoint surfaces it
unauthenticated so external probes can alert on `status: degraded` without
needing the bot API key.

### Realized P&L counter

`pnl.realized_pnl_today()` walks the `trade_audit` log and FIFO-matches BUYs to subsequent SELLs to compute today's realized P&L (UTC day). The risk gate's daily-loss check now combines Alpaca's `day_pl` with this audit-derived figure and uses **whichever is more pessimistic** — so a stale Alpaca account state during a hot-trading period can't hide losses from the floor. Same module's `realized_pnl_since(start_iso)` supports arbitrary windows for /trades/pnl extensions.

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

Constants live in `app/services/trading_engine.py` but most read through
`getattr(settings, "SHORT_TERM_*", default)`, so anything in the `SHORT_TERM_*`
column below can be overridden per-deploy in `.env`. The value shown is the
CODE DEFAULT — what runs on a fresh install with no override.

| Constant | Default | `.env` override | Meaning |
|---|---|---|---|
| `RSI_BUY_THRESHOLD` | 35 | `SHORT_TERM_RSI_BUY_THRESHOLD` | Buy when RSI < 35 (oversold) |
| `RSI_SELL_THRESHOLD` | 70 | `SHORT_TERM_RSI_SELL_THRESHOLD` | Sell when RSI > 70 (overbought) |
| `PROFIT_TARGET` | 0.50 | `SHORT_TERM_PROFIT_TARGET` | Exit option at +50% gain |
| `STOP_LOSS` | 0.30 | `SHORT_TERM_STOP_LOSS` | Exit option at -30% loss |
| `MAX_POSITIONS` | 5 | `SHORT_TERM_MAX_POSITIONS` | Max concurrent option trades |
| `IV_RANK_MAX` | 50 | `SHORT_TERM_IV_RANK_MAX` | Skip if HV rank > 50% |
| `EARNINGS_DAYS_MIN` | 7 | `SHORT_TERM_EARNINGS_BUFFER_DAYS` | Skip if earnings within 7 days |
| `TRAILING_STOP_ACTIVATION` | 0.20 | `SHORT_TERM_TRAILING_ACTIVATION` | Arm trailing stop at +20% |
| `TRAILING_STOP_PCT` | 0.15 | `SHORT_TERM_TRAILING_STOP_PCT` | Trailing stop = peak × (1 - 0.15) once armed |
| `OPTION_WEEKS_OUT` | 2 | `SHORT_TERM_OPTION_WEEKS_OUT` | Buy ~2-week expiry ATM calls |
| `DAYS_BEFORE_EXPIRY` | 3 | *(not overridable)* | Close 3 days before expiry |

> **Note:** earlier revisions of this doc listed `PROFIT_TARGET=0.60` and
> `OPTION_WEEKS_OUT=4`. Those were the values before the strategy was
> retuned for shorter-duration + tighter-target trades (commit history:
> the `getattr(..., SHORT_TERM_*)` shim was added later than the doc's
> original table). If you want the old profile back, set
> `SHORT_TERM_OPTION_WEEKS_OUT=4` and `SHORT_TERM_PROFIT_TARGET=0.60`
> in `.env` — no code change needed.
