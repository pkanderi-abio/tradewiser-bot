from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.routes import quotes, trades, health, watchlist
from app.core.logger import setup_logger
from app.services.trading_engine import start_trading_loop
import app.services.trading_engine as engine
from app.services.watchlist_manager import EXPERT_PICKS
from app.core.logger import logger
import asyncio


async def _populate_expert_watchlist():
    """Load expert stock picks into the trading watchlist.
    Options are generated dynamically at trade time — not pre-loaded.
    """
    engine.WATCHLIST.clear()
    for sym in EXPERT_PICKS.keys():
        engine.WATCHLIST.append(sym)
    logger.info(f"[STARTUP] Watchlist: {len(engine.WATCHLIST)} stocks (ATM options generated at trade time)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    # Start watchlist population in background so startup is instant.
    # The trading loop begins on the default 3 symbols and expands once
    # all 24 expert picks + ATM options have been fetched (~20-30s).
    app.state.watchlist_task = asyncio.create_task(_populate_expert_watchlist())
    app.state.trading_task   = asyncio.create_task(start_trading_loop())
    yield
    # Shutdown — cancel both tasks
    for attr in ("trading_task", "watchlist_task"):
        task = getattr(app.state, attr, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="TradeWiser Bot", version="1.0", lifespan=lifespan)

app.include_router(quotes.router)
app.include_router(trades.router)
app.include_router(health.router)
app.include_router(watchlist.router)
