# app/core/auth.py
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from app.core.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str = Security(_api_key_header)) -> None:
    """FastAPI dependency that enforces X-API-Key authentication."""
    if api_key != settings.BOT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
