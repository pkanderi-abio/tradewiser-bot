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

    # Groq API key — powers the AI trade advisor (Llama 3.3 70B, free tier).
    # Get yours at https://console.groq.com/ (no credit card required).
    # Leave empty to use Ollama local LLM (llama3.2 must be pulled first).
    GROQ_API_KEY: str = Field("", description="Groq API key for LLM trade decisions (free tier)")

    # Bot HTTP API key — set this to protect the REST endpoints.
    # Leave empty to disable endpoint authentication (not recommended in production).
    BOT_API_KEY: str = Field("", description="API key required on X-API-Key header for all bot endpoints")

    POLL_INTERVAL: int = 5

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
