from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/")
async def health_check():
    return {"status": "ok", "message": "TradeWiser is running"}


@router.get("/broker")
async def broker_health():
    """Live broker auth state. The bot can run silently when Alpaca is down —
    this is the endpoint to check when 'why no trades' comes up.

    Unauthenticated by design: this surfaces *only* connection state (no PII,
    no positions, no balances) and is meant to be safe for an external uptime
    check to poll. Account-level data still lives behind X-API-Key on
    /trades/pnl and /trades/strategy/status.
    """
    from app.services.alpaca_client import alpaca_client
    snap = alpaca_client.broker_snapshot()
    snap["status"] = "ok" if snap["authenticated"] else "degraded"
    return snap
