from fastapi import FastAPI
from app.routes import quotes, trades, health
from app.core.logger import setup_logger
from app.services.trading_engine import start_trading_loop
import asyncio

app = FastAPI(title="TradeWiser Bot", version="1.0")
setup_logger()

app.include_router(quotes.router)
app.include_router(trades.router)
app.include_router(health.router)

@app.on_event("startup")
async def startup_event():
    app.state.trading_task = asyncio.create_task(start_trading_loop())

@app.on_event("shutdown")
async def shutdown_event():
    task = getattr(app.state, "trading_task", None)
    if task and not task.done():
        task.cancel()
