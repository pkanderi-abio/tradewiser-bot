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
    # Trading Configuration
    # ============================================================
    TRADING_MAX_POSITION_SIZE: int = Field(1000, description="Maximum position size per symbol")
    TRADING_STOP_LOSS_PERCENT: float = Field(2.0, description="Default stop-loss percentage")
    TRADING_TAKE_PROFIT_PERCENT: float = Field(5.0, description="Default take-profit percentage")
    TRADING_MIN_CONFIDENCE: float = Field(0.60, description="Minimum confidence for trade execution")

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
