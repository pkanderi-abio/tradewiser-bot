from fastapi import APIRouter, Depends, HTTPException
from app.core.auth import require_api_key
from app.services.alpaca_client import alpaca_client

router = APIRouter(prefix="/quotes", tags=["quotes"], dependencies=[Depends(require_api_key)])

WATCHLIST = ["SPY", "QQQ", "AAPL"]

@router.get("/")
async def list_watchlist():
    return {"watchlist": WATCHLIST}

@router.get("/{symbol}")
async def get_quote(symbol: str):
    quote = alpaca_client.get_quote(symbol.upper())
    if not quote:
        raise HTTPException(status_code=502, detail=f"Unable to fetch quote for {symbol}")
    return {"symbol": symbol.upper(), "quote": quote}
