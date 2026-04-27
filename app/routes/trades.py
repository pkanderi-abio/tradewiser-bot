from datetime import datetime
from typing import Literal, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, conint, validator
from app.services.utils import get_audit_log, record_audit_entry
from app.services.trading_engine import momentum_strategy, MOMENTUM_THRESHOLD_BUY, MOMENTUM_THRESHOLD_SELL, MOMENTUM_WINDOW
from app.services.webull_client import webull_client

router = APIRouter(prefix="/trades", tags=["trades"])

class TradeRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    quantity: conint(gt=0)
    side: Literal["BUY", "SELL", "SHORT"] = "BUY"
    order_type: Literal["MKT", "LMT", "STP", "STP LMT", "STP TRAIL"] = "MKT"
    price: Optional[float] = None
    enforce: Literal["GTC", "DAY", "IOC"] = "GTC"
    outside_regular_trading_hour: bool = False
    stp_price: Optional[float] = None
    dry_run: bool = False

    @validator("price", always=True)
    def require_price_for_limit(cls, value, values):
        if values.get("order_type") == "LMT" and value is None:
            raise ValueError("price is required for limit orders")
        return value

    @validator("stp_price", always=True)
    def require_stp_price_for_stop(cls, value, values):
        if values.get("order_type") in {"STP", "STP LMT"} and value is None:
            raise ValueError("stp_price is required for stop orders")
        return value

@router.get("/status")
async def trades_status():
    return {"status": "ok", "message": "TradeWiser trade endpoints are available"}

@router.post("/execute")
async def execute_trade(order: TradeRequest):
    audit_entry = {
        "symbol": order.symbol.upper(),
        "quantity": order.quantity,
        "side": order.side,
        "order_type": order.order_type,
        "price": order.price,
        "enforce": order.enforce,
        "outside_regular_trading_hour": order.outside_regular_trading_hour,
        "stp_price": order.stp_price,
        "dry_run": order.dry_run,
        "submitted_at": datetime.utcnow().isoformat() + "Z",
    }

    if order.dry_run:
        audit_entry["status"] = "dry_run"
        saved_entry = record_audit_entry(audit_entry)
        return {
            "status": "dry_run",
            "message": "Order validated successfully but not submitted.",
            "order": {k: v for k, v in saved_entry.items() if k not in {"status", "id"}},
            "id": saved_entry["id"],
        }

    result = webull_client.place_order(
        symbol=order.symbol.upper(),
        quantity=order.quantity,
        side=order.side,
        order_type=order.order_type,
        price=order.price,
        enforce=order.enforce,
        outside_regular_trading_hour=order.outside_regular_trading_hour,
        stp_price=order.stp_price,
    )

    if result is None:
        audit_entry["status"] = "failed"
        audit_entry["detail"] = "Unable to place order; brokerage service unavailable"
        saved_entry = record_audit_entry(audit_entry)
        raise HTTPException(status_code=503, detail=audit_entry["detail"])

    audit_entry["status"] = "submitted"
    audit_entry["result"] = result
    saved_entry = record_audit_entry(audit_entry)
    return {"status": "submitted", "data": result, "id": saved_entry["id"]}

@router.get("/audit")
async def trade_audit(limit: int = 100):
    return {"status": "ok", "audit": get_audit_log(limit)}

@router.get("/audit/{entry_id}")
async def trade_audit_entry(entry_id: int):
    entry = get_audit_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    return {"status": "ok", "entry": entry}

@router.get("/current")
async def current_orders():
    result = webull_client.get_current_orders()
    if result is None:
        raise HTTPException(status_code=503, detail="Unable to fetch current orders; brokerage service unavailable")
    return {"status": "ok", "orders": result}

@router.get("/options/chain/{underlying_symbol}")
async def get_options_chain(underlying_symbol: str, expiration_date: Optional[str] = None):
    """Get options chain for an underlying symbol"""
    result = webull_client.get_options_chain(underlying_symbol, expiration_date)
    if result is None:
        raise HTTPException(status_code=503, detail="Unable to fetch options chain; brokerage service unavailable")
    return {"status": "ok", "chain": result}

@router.get("/strategy/status")
async def strategy_status():
    """Get momentum strategy status"""
    status = momentum_strategy.get_status()

    # Add current momentum calculations for all symbols
    momentum_data = {}
    from app.services.trading_engine import WATCHLIST
    for symbol in WATCHLIST:
        prices = list(momentum_strategy.price_history[symbol])
        if prices:
            current_price = prices[-1]
            momentum = momentum_strategy.calculate_momentum(symbol)
            momentum_data[symbol] = {
                "current_price": current_price,
                "momentum": momentum,
                "momentum_percent": f"{momentum:.2%}",
                "data_points": len(prices),
                "should_buy": momentum_strategy.should_buy(symbol, current_price),
                "should_sell": momentum_strategy.should_sell(symbol, current_price),
                "asset_class": "option" if symbol.startswith("O:") else "stock"
            }

    return {
        "status": "ok",
        "strategy": "momentum",
        "parameters": {
            "window": MOMENTUM_WINDOW,
            "buy_threshold": MOMENTUM_THRESHOLD_BUY,
            "sell_threshold": MOMENTUM_THRESHOLD_SELL,
            "trade_quantity": 1
        },
        "momentum_data": momentum_data,
        "positions": status["positions"],
        "last_trade_prices": status["last_trade_prices"]
    }
