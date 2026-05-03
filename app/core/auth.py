# app/core/auth.py
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from app.core.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str = Security(_api_key_header)) -> None:
    """FastAPI dependency that enforces X-API-Key authentication.

    Authentication is skipped when BOT_API_KEY is not configured (empty string),
    which allows unauthenticated access in development. Set BOT_API_KEY in .env
    for any deployment where the HTTP port is reachable by other processes.
    """
    if not settings.BOT_API_KEY:
        return
    if api_key != settings.BOT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
