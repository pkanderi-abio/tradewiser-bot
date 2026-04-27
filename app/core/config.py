# app/core/config.py

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_env_file() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return Path.cwd() / ".env"

ENV_FILE = find_env_file()

class Settings(BaseSettings):
    # Alpaca API credentials (much more reliable than Webull)
    ALPACA_API_KEY: str = Field(..., description="Alpaca API key")
    ALPACA_SECRET_KEY: str = Field(..., description="Alpaca secret key")
    ALPACA_BASE_URL: str = Field("https://paper-api.alpaca.markets", description="Alpaca API base URL (paper trading by default)")

    # Legacy Webull settings (kept for compatibility but not used)
    WEBULL_EMAIL: str = Field("", description="Webull account email (deprecated)")
    WEBULL_PASSWORD: str = Field("", description="Webull account password (deprecated)")
    WEBULL_DEVICE_NAME: str = Field("TradeWiserBot", description="Webull device name (deprecated)")

    POLL_INTERVAL: int = 5  # Reduced for more responsive auto-trading

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

# Create the global settings instance
settings = Settings()
