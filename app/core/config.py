# app/core/config.py

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_env_file() -> Path:
    """
    Find .env file, checking the known installation path first so a
    world-writable CWD (e.g. C:\\Windows\\System32 when running as a service)
    cannot shadow the real credentials file.

    Search order:
    1. Known installation directory (C:\\Program Files (x86)\\TradeWiser\\TradeWiser Bot)
    2. Directory containing this file (development checkout root)
    3. Current working directory (fallback for dev)
    """
    # 1. Known installation path — checked first to prevent CWD shadowing
    service_env = Path(r"C:\Program Files (x86)\TradeWiser\TradeWiser Bot\.env")
    if service_env.exists():
        return service_env

    # 2. Repo root relative to this file (app/core/config.py → ../../.env)
    repo_env = Path(__file__).resolve().parent.parent.parent / ".env"
    if repo_env.exists():
        return repo_env

    # 3. CWD fallback (development only)
    return Path.cwd() / ".env"


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
    # Scheduler Configuration
    # ============================================================
    SCHEDULER_ENABLED: bool = Field(True, description="Enable autonomous trading scheduler")
    SCHEDULER_INTERVAL_SECONDS: int = Field(60, description="Scheduler check interval (seconds)")
    SCHEDULER_MARKET_OPEN_DELAY: int = Field(300, description="Wait time after market open (seconds)")
    SCHEDULER_MARKET_CLOSE_BUFFER: int = Field(900, description="Stop trading before market close (seconds)")

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
