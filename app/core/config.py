# app/core/config.py

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_env_file() -> Path:
    """
    Find .env file. Prefers installation locations over CWD to prevent
    a world-writable working directory from shadowing real credentials.

    Search order (first match wins):
    1. Linux system location: /etc/tradewiser/.env
    2. Linux install location: /opt/tradewiser/.env
    3. Windows service location (legacy)
    4. Repo root (development checkout)
    5. Current working directory (last resort)
    """
    candidates = []

    # 1. Linux system-wide (recommended for production on Ubuntu)
    candidates.append(Path("/etc/tradewiser/.env"))

    # 2. Common Linux install dir
    candidates.append(Path("/opt/tradewiser/.env"))

    # 3. Windows service install dir (kept for compatibility)
    candidates.append(Path(r"C:\Program Files (x86)\TradeWiser\TradeWiser Bot\.env"))

    # 4. Development repo root (relative to this file)
    repo_env = Path(__file__).resolve().parent.parent.parent / ".env"
    candidates.append(repo_env)

    # 5. CWD fallback
    candidates.append(Path.cwd() / ".env")

    for p in candidates:
        if p.exists():
            return p

    # If none exist, return the most likely production location so Settings
    # can still raise a clear error if required keys are missing.
    return Path("/etc/tradewiser/.env")


ENV_FILE = find_env_file()


class Settings(BaseSettings):
    # Alpaca API credentials
    ALPACA_API_KEY: str = Field(..., description="Alpaca API key")
    ALPACA_SECRET_KEY: str = Field(..., description="Alpaca secret key")
    ALPACA_BASE_URL: str = Field("https://paper-api.alpaca.markets", description="Alpaca API base URL (paper trading by default)")
    ALPACA_AUTH_RETRY_COOLDOWN_SECONDS: int = Field(60, description="After an Alpaca auth failure, wait this long before retrying. Prevents API hammering on repeated calls while still allowing recovery from transient failures (the prior code latched forever).")

    # ============================================================
    # LLM Provider Configuration (Priority Order)
    # ============================================================
    # 1. OpenAI (gpt-4, gpt-3.5)
    OPENAI_API_KEY: str = Field("", description="OpenAI API key for GPT-4/GPT-3.5 access")
    OPENAI_MODEL: str = Field("gpt-4-turbo-preview", description="OpenAI model to use")
    OPENAI_BASE_URL: str = Field("https://api.openai.com/v1", description="OpenAI base URL")

    # 2. Anthropic Claude (Claude 3 Opus, Sonnet, Haiku)
    ANTHROPIC_API_KEY: str = Field("", description="Anthropic API key for Claude access")
    ANTHROPIC_MODEL: str = Field("claude-3-opus-20240229", description="Anthropic model to use")

    # 3. Groq (Llama 3.3 70B - free tier)
    # Get key at https://console.groq.com/ (no credit card required)
    GROQ_API_KEY: str = Field("", description="Groq API key for Llama 3.3 70B (free tier)")
    GROQ_MODEL: str = Field("llama-3.3-70b-versatile", description="Groq model to use")

    # 4. Ollama (Local, free forever)
    # Requires: ollama pull llama3.2
    OLLAMA_MODEL: str = Field("llama3.2", description="Ollama model to use")
    OLLAMA_URL: str = Field("http://localhost:11434/v1", description="Ollama API URL")
    OLLAMA_ENABLED: bool = Field(True, description="Enable local Ollama as fallback")

    # ============================================================
    # AI Trading Configuration
    # ============================================================
    AI_MIN_CONFIDENCE: float = Field(0.65, description="Minimum confidence threshold for AI trading signals")
    AI_DECISION_CACHE_TTL: int = Field(3600, description="Cache TTL for AI decisions (seconds)")
    AI_NEWS_CACHE_TTL: int = Field(300, description="Cache TTL for news data (seconds)")
    AI_SENTIMENT_ENABLED: bool = Field(True, description="Enable sentiment analysis")
    AI_NEWS_ENABLED: bool = Field(True, description="Enable news analysis")
    AI_MARKET_INTELLIGENCE_ENABLED: bool = Field(True, description="Enable market intelligence module")

    # ============================================================
    # AI Reliability Controls (production-grade execution safety)
    # ============================================================
    AI_KILL_SWITCH: bool = Field(False, description="When true, all AI decisions are forced to HOLD — emergency stop without restarting the service")
    AI_FAIL_CLOSED: bool = Field(True, description="When true, LLM errors return HOLD (block trade). When false, the proposed signal is approved at low confidence (NOT recommended for live trading)")
    AI_REQUEST_TIMEOUT_SECONDS: float = Field(15.0, description="Per-request LLM timeout — total wall-clock budget for one decision attempt")
    AI_MAX_RETRIES: int = Field(2, description="Retries after first attempt on transient LLM failures (network, 5xx, rate limit)")
    AI_RETRY_BACKOFF_SECONDS: float = Field(0.75, description="Initial backoff between retries; doubles each attempt")
    AI_CIRCUIT_BREAKER_THRESHOLD: int = Field(5, description="Consecutive LLM failures before circuit opens and all calls short-circuit to HOLD")
    AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = Field(120, description="How long the circuit stays open before a single probe call is allowed")
    AI_AUDIT_DB_PATH: str = Field("tradewiser_audit.db", description="SQLite path for trade and AI decision audit. Use ':memory:' in tests.")
    AI_MAX_NEWS_HEADLINES: int = Field(6, description="Headlines passed to the LLM — capped for prompt-injection budget and cost")
    AI_MAX_HEADLINE_CHARS: int = Field(160, description="Per-headline character cap after sanitization")

    # ============================================================
    # News Severity / Event Configuration
    # Two related but distinct layers:
    #   1. NEWS_SEVERITY_*     — lightweight aggregate for AI context + legacy RSI boosting
    #   2. NEWS_EVENT_* + NEWS_STRATEGY_* — full production NewsEventExtractor + NewsEventStrategy
    # ============================================================
    # Lightweight severity (used by AIAdvisor prompt + news_severity_gate + RSI boost)
    NEWS_SEVERITY_ENABLED: bool = Field(True, description="Enable per-headline LLM severity scoring (event_type + -10..+10 score)")
    NEWS_SEVERITY_LOOKBACK_DAYS: int = Field(3, description="Window for aggregating recent headline severity scores")
    NEWS_SEVERITY_MIN_AGGREGATE: float = Field(4.0, description="Aggregate severity threshold considered 'notable' (passed to AI for context)")
    NEWS_SEVERITY_MAX_TO_SCORE: int = Field(15, description="Max headlines to score per symbol per call (cost control)")
    NEWS_SEVERITY_AGGREGATE: str = Field("sum", description="Aggregation for severity scores: 'sum' (default) or 'mean'")
    NEWS_SEVERITY_BOOST_FACTOR: float = Field(2.0, description="Factor by which positive severity aggregate lowers effective RSI for signal boosting in trading engine (higher = stronger effect)")

    # Full News Event Extractor (hardened, used by NewsEventStrategy)
    NEWS_EVENT_KILL_SWITCH: bool = Field(False, description="Emergency stop — force extract() to return an empty list without hitting the LLM")
    NEWS_EVENT_FAIL_CLOSED: bool = Field(True, description="On extractor failure, return no events (no signal). Set False only for non-live testing.")
    NEWS_EVENT_REQUEST_TIMEOUT_SECONDS: float = Field(15.0, description="Per-batch LLM timeout")
    NEWS_EVENT_MAX_RETRIES: int = Field(2, description="Retries after first attempt on transient LLM failures")
    NEWS_EVENT_RETRY_BACKOFF_SECONDS: float = Field(0.75, description="Initial backoff between retries; doubles each attempt")
    NEWS_EVENT_CIRCUIT_BREAKER_THRESHOLD: int = Field(5, description="Consecutive extractor failures before circuit opens")
    NEWS_EVENT_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = Field(120, description="Cooldown before a probe call is allowed")
    NEWS_EVENT_CACHE_TTL_SECONDS: int = Field(3600, description="Per-headline extraction cache TTL")
    NEWS_EVENT_BATCH_SIZE: int = Field(5, description="Headlines per LLM call — smaller batches give better JSON discipline")
    NEWS_EVENT_MAX_HEADLINES_PER_CALL: int = Field(30, description="Ceiling on headlines passed to extract() per symbol per pass")
    NEWS_EVENT_MIN_ABS_SEVERITY: int = Field(3, description="Drop events with |severity| below this threshold before aggregation")

    # NewsEvent Strategy configuration
    NEWS_STRATEGY_ENABLED: bool = Field(False, description="Enable the full NewsEventStrategy (multi-day event driven positions). When False it still manages exits on existing positions.")
    NEWS_STRATEGY_SEVERITY_MIN_TO_ENTER: float = Field(4.0, description="Aggregate severity (per NEWS_SEVERITY_AGGREGATE) required to enter a new position")
    NEWS_STRATEGY_SEVERITY_MIN_OPTIONS: float = Field(7.0, description="Aggregate severity at/above which to route to ATM call options (leveraged conviction). Below this + above ENTER threshold => stock.")
    NEWS_STRATEGY_HOLD_DAYS: int = Field(5, description="Default hold period in trading days for NewsEventStrategy positions")
    NEWS_STRATEGY_STOP_LOSS_PCT: float = Field(0.08, description="Stop loss as fraction of entry price")
    NEWS_STRATEGY_TAKE_PROFIT_PCT: float = Field(0.15, description="Take profit as fraction of entry price")
    NEWS_STRATEGY_MAX_CONCURRENT: int = Field(3, description="Max concurrent open positions across all NewsEventStrategy instruments")
    NEWS_STRATEGY_REVERSAL_SEVERITY_MULT: float = Field(-0.75, description="Exit if new aggregate severity has opposite sign and |severity| exceeds |entry severity| * this multiplier")
    NEWS_STRATEGY_POSITION_DOLLARS: float = Field(1000.0, description="Notional target per news-strategy position (equal-weight sizing v1)")

    # (See grouped block above for the full set of NEWS_EVENT_* and NEWS_STRATEGY_* settings)

    # ============================================================
    # Multi-model Ensemble — Stage-1 screen (cheap/fast) → Stage-2 confirm (smart)
    # ============================================================
    ENSEMBLE_ENABLED: bool = Field(True, description="Send borderline / high-stakes BUYs through a smart-model second opinion when an Anthropic or OpenAI key is configured")
    ENSEMBLE_CONFIRM_BAND_LOW: float = Field(0.65, description="If stage-1 confidence is at/above MIN_CONFIDENCE but below ENSEMBLE_CONFIRM_BAND_HIGH, send to stage-2 for confirmation")
    ENSEMBLE_CONFIRM_BAND_HIGH: float = Field(0.85, description="Above this confidence the stage-1 model is trusted on its own — no second opinion required")
    ENSEMBLE_PROVIDER: str = Field("auto", description="Stage-2 provider: 'auto' (anthropic > openai > none), 'anthropic', 'openai', 'none'")
    ENSEMBLE_ANTHROPIC_MODEL: str = Field("claude-sonnet-4-6", description="Anthropic model used for stage-2 confirm")
    ENSEMBLE_OPENAI_MODEL: str = Field("gpt-4o-mini", description="OpenAI model used for stage-2 confirm")

    # ============================================================
    # Pre-trade Risk Gate (runs after AI approval, before order placement)
    # ============================================================
    RISK_GATE_ENABLED: bool = Field(True, description="Master switch for the pre-trade risk gate")
    RISK_MAX_SYMBOL_CONCENTRATION_PCT: float = Field(25.0, description="Reject BUY if proposed position would push one symbol above this % of account equity")
    RISK_MAX_DAILY_LOSS_DOLLARS: float = Field(500.0, description="Halt new BUYs if today's realized + unrealized P&L falls below -this value")
    RISK_MAX_DRAWDOWN_PCT: float = Field(15.0, description="Halt new BUYs if account equity is this % below the rolling peak")
    RISK_PEAK_EQUITY_WINDOW_DAYS: int = Field(30, description="Drawdown calculated against the rolling N-day peak equity snapshot")
    RISK_HALT_BLOCKS_SELLS: bool = Field(False, description="If true, daily-loss / drawdown halt also blocks SELLs. Default false — always allow exits.")

    # ============================================================
    # Market Regime Gate (skip the RSI strategy in adverse conditions)
    # ============================================================
    REGIME_GATE_ENABLED: bool = Field(True, description="Master switch for the market-regime gate")
    REGIME_VIX_PANIC_LEVEL: float = Field(35.0, description="Skip new BUYs if VIX is above this level (broad-market panic)")
    REGIME_VIX_ELEVATED_LEVEL: float = Field(25.0, description="Threshold for the 'elevated' VIX band; logged but not blocking by default")
    REGIME_BLOCK_ON_DOWNTREND: bool = Field(True, description="Skip new BUYs when SPY is in a confirmed downtrend (price < SMA50 < SMA200)")
    REGIME_BLOCK_ON_PANIC_VIX: bool = Field(True, description="Skip new BUYs when VIX > REGIME_VIX_PANIC_LEVEL")

    # ============================================================
    # Trading Configuration
    # ============================================================
    TRADING_MAX_POSITION_SIZE: int = Field(1000, description="Maximum position size per symbol")
    TRADING_STOP_LOSS_PERCENT: float = Field(2.0, description="Default stop-loss percentage")
    TRADING_TAKE_PROFIT_PERCENT: float = Field(5.0, description="Default take-profit percentage")
    TRADING_MIN_CONFIDENCE: float = Field(0.60, description="Minimum confidence for trade execution")

    # Strategy filters
    STRATEGY_REQUIRE_UPTREND_FILTER: bool = Field(True, description="When true, BUY signals require price > SMA50 AND (near-SMA50 OR above-avg-volume). When false, the RSI-only condition is sufficient. Set to false to allow buying oversold names in confirmed downtrends — higher trade frequency but no protection against catching falling knives. Other gates (regime, AI, risk) still apply.")

    # ============================================================
    # Short Term Options Strategy (Daily RSI + momentum → ATM call options)
    # This is the primary "short term + options" path. Always uses options
    # for leverage on short-duration moves. Separate from the multi-day NewsEventStrategy.
    # ============================================================
    SHORT_TERM_ENABLED: bool = Field(True, description="Master switch for the short-term RSI options strategy")
    SHORT_TERM_RSI_BUY_THRESHOLD: int = Field(35, description="RSI below this triggers BUY of ATM call (oversold short-term bounce)")
    SHORT_TERM_RSI_SELL_THRESHOLD: int = Field(70, description="RSI above this triggers SELL of the call (overbought)")
    SHORT_TERM_OPTION_WEEKS_OUT: int = Field(2, description="Target ~N-week expiry ATM calls for short-term gamma exposure (shorter than multi-day news positions)")
    SHORT_TERM_PROFIT_TARGET: float = Field(0.50, description="Close short-term option position at this gain (e.g. 50%)")
    SHORT_TERM_STOP_LOSS: float = Field(0.30, description="Hard stop on short-term option at this loss")
    SHORT_TERM_MAX_POSITIONS: int = Field(5, description="Max concurrent short-term option positions")
    SHORT_TERM_IV_RANK_MAX: int = Field(50, description="Skip short-term option buys if HV rank is this high (expensive vol)")
    SHORT_TERM_EARNINGS_BUFFER_DAYS: int = Field(7, description="Avoid short-term option entries if earnings within N days")
    SHORT_TERM_TRAILING_ACTIVATION: float = Field(0.20, description="Arm trailing stop on short-term option after this gain")
    SHORT_TERM_TRAILING_STOP_PCT: float = Field(0.15, description="Trailing stop floor for short-term options once armed")

    # ============================================================
    # Scheduler Configuration
    # ============================================================
    SCHEDULER_ENABLED: bool = Field(True, description="Enable autonomous trading scheduler")
    SCHEDULER_INTERVAL_SECONDS: int = Field(60, description="Scheduler check interval (seconds)")
    SCHEDULER_MARKET_OPEN_DELAY: int = Field(300, description="Wait time after market open (seconds)")
    SCHEDULER_MARKET_CLOSE_BUFFER: int = Field(900, description="Stop trading before market close (seconds)")

    # ============================================================
    # Market-hours gate + stale-order reaper
    # ============================================================
    # Motivation: on 2026-07-18 (Saturday) the loop spun for hours emitting
    # [EXIT-COOLDOWN] / [SELL-DEFERRED] on an AXP option whose SELL was queued
    # after Friday's close and could not fill until Monday. Two knobs:
    #   1. When the market is closed, skip the entire BUY+EXIT evaluation and
    #      sleep MARKET_CLOSED_POLL_SECONDS instead of the 60s live cadence.
    #   2. Auto-cancel option SELL orders older than STALE_SELL_ORDER_MAX_AGE_MINUTES
    #      so a bad-priced limit doesn't lock a position indefinitely.
    MARKET_HOURS_GATE_ENABLED: bool = Field(True, description="Skip trading-loop evaluation when Alpaca reports the market is closed. Set false only for extended-hours experiments (options don't trade extended anyway).")
    MARKET_CLOSED_POLL_SECONDS: int = Field(900, description="Loop sleep interval while the market is closed (default 15 min). During market hours the loop stays at SCHEDULER_INTERVAL_SECONDS.")
    MARKET_CLOCK_CACHE_SECONDS: int = Field(60, description="TTL on the Alpaca clock check. Prevents hammering /v2/clock every iteration.")
    STALE_SELL_ORDER_MAX_AGE_MINUTES: int = Field(30, description="Auto-cancel unfilled option SELL orders older than this before deferring on 'existing open order' — prevents a stale limit from locking a position across sessions. 0 disables the reaper.")

    # Bot HTTP API key — must be set to a non-placeholder value in .env before starting
    BOT_API_KEY: str = Field("dev-key-disabled", description="API key required on X-API-Key header for bot endpoints; set a strong random value in .env")

    POLL_INTERVAL: int = 5

    # ============================================================
    # Logging Configuration
    # ============================================================
    LOG_LEVEL: str = Field("INFO", description="Logging level")
    LOG_FILE: str = Field("tradewiser.log", description="Log file path")
    LOG_FORMAT: str = Field("%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="Log format")

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
