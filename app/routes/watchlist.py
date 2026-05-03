from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.core.auth import require_api_key
from app.services.watchlist_manager import (
    EXPERT_PICKS,
    scan_news_sentiment_async,
    get_atm_option_symbols,
)
import app.services.trading_engine as engine

router = APIRouter(
    prefix="/watchlist",
    tags=["watchlist"],
    dependencies=[Depends(require_api_key)],
)

_ALL_CATEGORIES = sorted({info["category"] for info in EXPERT_PICKS.values()})


class AddSymbolRequest(BaseModel):
    symbol: str
    include_options: bool = False
    options_weeks_out: int = 2


@router.get("/experts")
async def list_expert_picks(
    category: Optional[str] = Query(None, description=f"Filter by category: {', '.join(_ALL_CATEGORIES)}"),
):
    """Return all expert-curated symbols, optionally filtered by investment category."""
    picks = [
        {"symbol": sym, **info}
        for sym, info in EXPERT_PICKS.items()
        if category is None or info.get("category") == category
    ]
    return {
        "status": "ok",
        "categories": _ALL_CATEGORIES,
        "count": len(picks),
        "picks": picks,
    }


@router.get("/scan")
async def scan_expert_watchlist(
    category: Optional[str] = Query(None, description="Limit scan to one category"),
    top_n: int = Query(10, ge=1, le=50, description="Number of top results to return"),
):
    """
    Score expert-curated symbols by recent news sentiment.
    Returns symbols ranked bullish-to-bearish. Takes ~5-15 s (network I/O).
    """
    symbols = [
        sym for sym, info in EXPERT_PICKS.items()
        if category is None or info.get("category") == category
    ]
    if not symbols:
        raise HTTPException(status_code=400, detail=f"Unknown category '{category}'. Valid: {_ALL_CATEGORIES}")

    results = await scan_news_sentiment_async(symbols)
    return {
        "status": "ok",
        "scanned": len(symbols),
        "top_n": top_n,
        "results": results[:top_n],
    }


@router.get("/options/{symbol}")
async def get_options_for_symbol(
    symbol: str,
    weeks_out: int = Query(2, ge=1, le=8, description="Weeks to expiration (1=next Friday, 2=following Friday, …)"),
):
    """
    Generate near-ATM call and put OCC symbols for any underlying.
    Uses live price from yfinance to pick the at-the-money strike.
    """
    syms = get_atm_option_symbols(symbol.upper(), weeks_out)
    if not syms:
        raise HTTPException(
            status_code=404,
            detail=f"Could not generate options symbols for {symbol.upper()}. Verify the symbol and try again.",
        )
    return {
        "status": "ok",
        "underlying": symbol.upper(),
        "weeks_out": weeks_out,
        "options": syms,
    }


@router.get("/active")
async def get_active_watchlist():
    """Return all symbols currently being traded by the momentum bot."""
    stocks  = [s for s in engine.WATCHLIST if not s.startswith("O:")]
    options = [s for s in engine.WATCHLIST if s.startswith("O:")]
    return {
        "status": "ok",
        "total": len(engine.WATCHLIST),
        "stocks": stocks,
        "options": options,
    }


@router.post("/active")
async def add_to_watchlist(req: AddSymbolRequest):
    """
    Add a symbol to the active trading watchlist.
    Set include_options=true to also add the nearest ATM call and put.
    """
    sym = req.symbol.upper()
    added: list[str] = []

    if sym not in engine.WATCHLIST:
        engine.WATCHLIST.append(sym)
        added.append(sym)

    if req.include_options:
        for opt_sym in get_atm_option_symbols(sym, req.options_weeks_out):
            if opt_sym not in engine.WATCHLIST:
                engine.WATCHLIST.append(opt_sym)
                added.append(opt_sym)

    return {
        "status": "ok",
        "added": added,
        "watchlist": list(engine.WATCHLIST),
    }


@router.delete("/active/{symbol}")
async def remove_from_watchlist(symbol: str):
    """
    Remove a symbol (and any of its options) from the active trading watchlist.
    Passing SPY also removes all O:SPY… option contracts.
    """
    sym = symbol.upper()
    root = sym[2:] if sym.startswith("O:") else sym
    to_remove = [s for s in engine.WATCHLIST if s == sym or s.startswith(f"O:{root}")]

    if not to_remove:
        raise HTTPException(status_code=404, detail=f"{sym} not found in active watchlist")

    for s in to_remove:
        engine.WATCHLIST.remove(s)

    return {
        "status": "ok",
        "removed": to_remove,
        "watchlist": list(engine.WATCHLIST),
    }
