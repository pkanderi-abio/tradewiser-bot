from datetime import datetime, timezone
from typing import Annotated, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from annotated_types import Gt, Le
from app.core.auth import require_api_key
from app.core.config import settings
from app.services.utils import (
    get_audit_log, get_audit_entry, record_audit_entry,
    get_ai_decisions, ai_decision_stats, ai_token_stats,
    get_risk_events, get_news_events, news_event_stats,
)
from app.services.trading_engine import (
    momentum_strategy,
    RSI_BUY_THRESHOLD, RSI_SELL_THRESHOLD, RSI_PERIOD, SMA_PERIOD,
    PROFIT_TARGET, STOP_LOSS, DAYS_BEFORE_EXPIRY, OPTION_WEEKS_OUT,
    MAX_POSITIONS, IV_RANK_MAX, EARNINGS_DAYS_MIN,
    TRAILING_STOP_ACTIVATION, TRAILING_STOP_PCT,
    _days_to_expiry,
)
from app.services.alpaca_client import alpaca_client

router = APIRouter(prefix="/trades", tags=["trades"], dependencies=[Depends(require_api_key)])

class TradeRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    quantity: Annotated[int, Gt(0), Le(settings.TRADING_MAX_POSITION_SIZE)]
    side: Literal["BUY", "SELL", "SHORT"] = "BUY"
    order_type: Literal["MKT", "LMT", "STP", "STP LMT", "STP TRAIL"] = "MKT"
    price: Optional[float] = None
    enforce: Literal["GTC", "DAY", "IOC"] = "GTC"
    outside_regular_trading_hour: bool = False
    stp_price: Optional[float] = None
    dry_run: bool = False

    @model_validator(mode="after")
    def check_order_type_fields(self) -> "TradeRequest":
        if self.order_type == "LMT" and self.price is None:
            raise ValueError("price is required for limit orders")
        if self.order_type in {"STP", "STP LMT"} and self.stp_price is None:
            raise ValueError("stp_price is required for stop orders")
        return self

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
        "submitted_at": datetime.now(timezone.utc).isoformat(),
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

    result = alpaca_client.place_order(
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
        broker_err = alpaca_client.last_order_error
        audit_entry["status"] = "failed"
        audit_entry["detail"] = broker_err or "Unable to place order; brokerage service unavailable"
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

@router.get("/pnl")
async def pnl_summary():
    """Account P&L plus per-position unrealized P&L from Alpaca."""
    account = alpaca_client.get_account_pnl()
    if account is None:
        raise HTTPException(status_code=503, detail="Unable to fetch P&L; brokerage service unavailable")

    positions = alpaca_client.get_positions_pnl() or []

    # Realized P&L estimated from audit log using FIFO matching of buys/sells
    realized_pl = 0.0
    buys: dict = {}
    for entry in get_audit_log(limit=1000):
        sym   = entry.get("symbol", "")
        side  = entry.get("side", "")
        qty   = entry.get("quantity", 0) or 0
        price = float((entry.get("result") or {}).get("filled_avg_price") or entry.get("price") or 0)
        if not price:
            continue
        if side == "BUY":
            buys.setdefault(sym, []).append({"qty": qty, "price": price})
        elif side in ("SELL", "SHORT") and buys.get(sym):
            buy = buys[sym].pop(0)
            realized_pl += (price - buy["price"]) * min(qty, buy["qty"])

    return {
        "status": "ok",
        "account": account,
        "realized_pl": round(realized_pl, 2),
        "open_positions": len(positions),
        "positions": positions,
    }

@router.get("/current")
async def current_orders():
    result = alpaca_client.get_current_orders()
    if result is None:
        raise HTTPException(status_code=503, detail="Unable to fetch current orders; brokerage service unavailable")
    return {"status": "ok", "orders": result}

@router.get("/options/chain/{underlying_symbol}")
async def get_options_chain(underlying_symbol: str, expiration_date: Optional[str] = None):
    """Get options chain for an underlying symbol"""
    result = alpaca_client.get_options_chain(underlying_symbol, expiration_date)
    if result is None:
        raise HTTPException(status_code=503, detail="Unable to fetch options chain; brokerage service unavailable")
    return {"status": "ok", "chain": result}

@router.get("/ai-status")
async def ai_status(window_minutes: int = 60, recent_limit: int = 20, tokens_window_minutes: int = 1440):
    """AI advisor state: provider, model, circuit breaker, recent decisions, error rate, token spend."""
    from app.services.ai_advisor import ai_advisor
    from app.services.news_severity_gate import news_severity_gate
    import time
    cached = {}
    now = time.time()
    for sym, (ts, decision) in ai_advisor._decision_cache.items():
        age = int(now - ts)
        cached[sym] = {**decision, "age_seconds": age}
    return {
        "status": "ok",
        "advisor": ai_advisor.snapshot(),
        "news_severity": news_severity_gate.snapshot(),
        "cached_decisions": cached,
        "stats": ai_decision_stats(window_minutes=window_minutes),
        "tokens": ai_token_stats(window_minutes=tokens_window_minutes),
        "recent": get_ai_decisions(limit=recent_limit),
    }


@router.get("/news-severity")
async def news_severity(symbol: Optional[str] = None, limit: int = 50):
    """News severity scoring status and recent headlines scores (ported from experiment)."""
    from app.services.news_severity_gate import news_severity_gate
    from app.services.utils import get_headline_scores

    config = news_severity_gate.snapshot()
    if symbol:
        scores = get_headline_scores(symbol.upper(), limit=limit)
        dec = news_severity_gate.evaluate(symbol.upper())
        return {
            "status": "ok",
            "symbol": symbol.upper(),
            "config": config,
            "decision": dec.to_dict(),
            "scores": scores,
        }
    return {
        "status": "ok",
        "config": config,
    }


@router.get("/news-severity/score")
async def news_severity_score(symbol: str, limit: int = 20):
    """Force (re)score headlines for a symbol and return latest decision + scores. Useful for testing / on-demand."""
    from app.services.news_severity_gate import news_severity_gate
    from app.services.utils import get_headline_scores
    from app.services.news_analyzer import NewsAnalyzer
    from app.services.news_feed import news_feed

    symbol = symbol.upper()
    # Force fresh headlines and score
    headlines = news_feed.headlines(symbol)  # cached but fresh enough
    analyzer = NewsAnalyzer()
    scored = analyzer.score_headline_severities(symbol, headlines)
    dec = news_severity_gate.evaluate(symbol)
    scores = get_headline_scores(symbol, limit=limit)
    return {
        "status": "ok",
        "symbol": symbol,
        "decision": dec.to_dict(),
        "scored_now": scored,
        "recent_db_scores": scores,
    }


@router.get("/news-severity/aggregate")
async def news_severity_aggregate(symbol: str):
    """Quick aggregate severity for a symbol (uses cached scores if available, else computes)."""
    from app.services.news_severity_gate import news_severity_gate
    from app.services.news_analyzer import NewsAnalyzer
    from app.services.news_feed import news_feed

    symbol = symbol.upper()
    dec = news_severity_gate.evaluate(symbol)
    # If no scores, force one computation
    if dec.scored_count == 0:
        headlines = news_feed.headlines(symbol)
        analyzer = NewsAnalyzer()
        scored = analyzer.score_headline_severities(symbol, headlines)
        agg = analyzer.aggregate_severity(scored)
        dec.aggregate = round(agg, 1)
        dec.scored_count = len(scored)
    return {
        "status": "ok",
        "symbol": symbol,
        "aggregate": dec.aggregate,
        "decision": dec.to_dict(),
    }


@router.get("/risk-status")
async def risk_status(recent_limit: int = 20):
    """Pre-trade risk gate posture: equity, day P&L, drawdown, concentration, recent events."""
    from app.services.risk_gate import risk_gate
    return {
        "status": "ok",
        "risk": risk_gate.snapshot(),
        "recent_events": get_risk_events(limit=recent_limit),
    }


@router.get("/market-regime")
async def market_regime():
    """Macro regime classification — VIX, SPY trend, and whether new BUYs are gated."""
    from app.services.regime import regime_gate
    return {"status": "ok", **regime_gate.snapshot()}

@router.get("/strategy/status")
async def strategy_status():
    """Get momentum strategy status"""
    status = momentum_strategy.get_status()

    # Enrich each stock's signal data with live position info
    signal_data = {}
    from app.services.trading_engine import WATCHLIST
    for symbol in WATCHLIST:
        sig        = status["signals"].get(symbol, {})
        opt_sym    = status["option_symbols"].get(symbol)
        entry_opt  = status["entry_opt_prices"].get(symbol)
        peak_opt   = status["peak_opt_prices"].get(symbol)
        days_left  = _days_to_expiry(opt_sym) if opt_sym else None
        signal_data[symbol] = {
            "signal":           sig.get("signal", "NONE"),
            "rsi":              sig.get("rsi"),
            "sma50":            sig.get("sma50"),
            "price":            sig.get("price"),
            "near_sma50":       sig.get("near_sma50"),
            "vol_above_avg":    sig.get("vol_above_avg"),
            "hv_rank":          sig.get("hv_rank"),
            "days_to_earnings": sig.get("days_to_earnings"),
            "holding_option":   opt_sym,
            "entry_opt_price":  entry_opt,
            "peak_opt_price":   peak_opt,
            "days_to_expiry":   days_left,
        }

    return {
        "status":             "ok",
        "strategy":           "Daily RSI → ATM call options",
        "active_positions":   momentum_strategy.active_position_count(),
        "parameters": {
            "rsi_period":              RSI_PERIOD,
            "rsi_buy_threshold":       RSI_BUY_THRESHOLD,
            "rsi_sell_threshold":      RSI_SELL_THRESHOLD,
            "sma_period":              SMA_PERIOD,
            "profit_target":           f"+{PROFIT_TARGET:.0%}",
            "stop_loss":               f"-{STOP_LOSS:.0%}",
            "option_weeks_out":        OPTION_WEEKS_OUT,
            "days_before_expiry_exit": DAYS_BEFORE_EXPIRY,
            "max_positions":           MAX_POSITIONS,
            "iv_rank_max":             f"{IV_RANK_MAX}%",
            "earnings_days_min":       EARNINGS_DAYS_MIN,
            "trailing_stop_activation": f"+{TRAILING_STOP_ACTIVATION:.0%}",
            "trailing_stop_pct":       f"-{TRAILING_STOP_PCT:.0%}",
        },
        "last_signal_date":   status["last_signal_date"],
        "positions":          status["positions"],
        "option_symbols":     status["option_symbols"],
        "peak_opt_prices":    status["peak_opt_prices"],
        "signal_data":        signal_data,
    }


# ── NewsEventStrategy endpoints (Phase 6) ──────────────────────────────────

@router.get("/news-strategy")
async def news_strategy_status(window_days: int = 30):
    """Live NewsEventStrategy state + calibration report + extractor health.

    window_days controls the calibration lookback (default 30d). The response
    layers strategy config, current position counts, calibration analysis, and
    extractor telemetry - one call for the dashboard.
    """
    from app.services.news_event_strategy import news_event_strategy, STRATEGY_NAME
    from app.services.news_event_extractor import news_event_extractor
    from app.services.news_strategy_calibration import news_strategy_calibration
    from app.services.position_manager import position_manager
    strategy_snap = news_event_strategy.snapshot()
    extractor_snap = news_event_extractor.snapshot()
    calibration = news_strategy_calibration.compute(window_days=window_days).to_dict()
    open_positions = [
        {
            "id": p.id, "symbol": p.symbol, "underlying": p.underlying,
            "instrument": p.instrument, "state": p.state,
            "entry_price": p.entry_price, "shares": p.shares,
            "stop_level": p.stop_level, "target_level": p.target_level,
            "hold_until": p.hold_until,
            "entry_severity": p.entry_severity, "entry_event_type": p.entry_event_type,
            "entry_reason": p.entry_reason,
        }
        for p in position_manager.list_positions(strategy=STRATEGY_NAME, limit=50)
        if p.state != "closed"
    ]
    return {
        "status": "ok",
        "strategy": strategy_snap,
        "extractor": extractor_snap,
        "open_positions": open_positions,
        "calibration": calibration,
        "audit_stats": news_event_stats(window_minutes=window_days * 1440),
    }


@router.get("/news-strategy/positions")
async def news_strategy_positions(
    state: Optional[Literal["pending", "open", "closed"]] = None,
    limit: int = 100,
):
    """List NewsEventStrategy positions with optional state filter."""
    from app.services.news_event_strategy import STRATEGY_NAME
    from app.services.position_manager import position_manager
    positions = position_manager.list_positions(strategy=STRATEGY_NAME, state=state, limit=limit)
    return {
        "status": "ok", "n": len(positions),
        "positions": [
            {
                "id": p.id, "symbol": p.symbol, "underlying": p.underlying,
                "instrument": p.instrument, "state": p.state,
                "entry_signal_at": p.entry_signal_at,
                "entry_filled_at": p.entry_filled_at,
                "entry_price": p.entry_price, "shares": p.shares,
                "stop_level": p.stop_level, "target_level": p.target_level,
                "hold_until": p.hold_until,
                "entry_severity": p.entry_severity, "entry_event_type": p.entry_event_type,
                "entry_reason": p.entry_reason,
                "exit_price": p.exit_price, "exit_reason": p.exit_reason,
                "exit_filled_at": p.exit_filled_at,
                "realized_pnl": p.realized_pnl,
                "last_updated_at": p.last_updated_at,
            }
            for p in positions
        ],
    }


@router.get("/news-events")
async def news_events(
    symbol: Optional[str] = None,
    outcome: Optional[str] = "ok",
    limit: int = 50,
):
    """Recent news event extractions (audit log rows).

    Default outcome='ok' returns successful extractions. Pass outcome=None (or
    a specific failure like 'schema_error') to see the raw stream.
    """
    return {
        "status": "ok",
        "events": get_news_events(symbol=symbol, outcome=outcome, limit=limit),
    }
