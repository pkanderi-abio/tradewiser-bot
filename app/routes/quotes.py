from fastapi import APIRouter, HTTPException
from app.services.webull_client import webull_client

router = APIRouter(prefix="/quotes", tags=["quotes"])

WATCHLIST = ["SPY", "QQQ", "AAPL"]

@router.get("/")
async def list_watchlist():
    return {"watchlist": WATCHLIST}

@router.get("/{symbol}")
async def get_quote(symbol: str):
    quote = webull_client.get_quote(symbol.upper())
    if not quote:
        raise HTTPException(status_code=502, detail=f"Unable to fetch quote for {symbol}")
    return {"symbol": symbol.upper(), "quote": quote}
