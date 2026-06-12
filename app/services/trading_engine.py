import asyncio
import re
from collections import defaultdict
from datetime import datetime, timezone, date
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from app.services.alpaca_client import alpaca_client
from app.services.utils import record_audit_entry
from app.services.ai_advisor import ai_advisor, MIN_CONFIDENCE
from app.services.risk_gate import risk_gate
from app.services.regime import regime_gate
from app.core.logger import logger
from app.core.config import settings


def _estimate_option_notional(stock_price: float, qty: int) -> float:
    """ATM ~4-week call premiums run roughly 3–5% of the underlying; we use 5%
    as a conservative overestimate so the concentration check leans cautious.
    Multiplier 100 = standard equity option contract size."""
    return max(0.0, float(stock_price) * 0.05 * 100 * qty)

# Underlying stocks only — options are generated at trade time
WATCHLIST: List[str] = ["SPY", "QQQ", "AAPL"]

# ── Strategy parameters ────────────────────────────────────────────────────────
RSI_PERIOD         = 14
RSI_BUY_THRESHOLD  = 35     # RSI < 35 = oversold → buy ATM call
RSI_SELL_THRESHOLD = 70     # RSI > 70 = overbought → exit call
SMA_PERIOD         = 50     # price must be above 50-day SMA (uptrend filter)
OPTION_WEEKS_OUT   = 4      # buy options expiring ~4 weeks out
PROFIT_TARGET      = 0.60   # close position at +60% option gain
STOP_LOSS          = 0.30   # close position at -30% option loss
DAYS_BEFORE_EXPIRY = 3      # close position this many days before expiry
TRADE_QUANTITY     = 1

# ── Position & risk controls ───────────────────────────────────────────────────
MAX_POSITIONS            = 5     # max concurrent option positions
IV_RANK_MAX              = 50    # skip BUY if 30-day HV rank > 50% (options expensive)
EARNINGS_DAYS_MIN        = 7     # skip BUY if earnings within this many calendar days
TRAILING_STOP_ACTIVATION = 0.20  # arm trailing stop after +20% gain
TRAILING_STOP_PCT        = 0.15  # trail 15% below the peak mark price


# ── Technical indicator helpers ────────────────────────────────────────────────

def _compute_rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta    = close.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, float("nan"))
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _get_hv_rank_from_hist(hist: pd.DataFrame) -> Optional[float]:
    """30-day HV rank (0–100) over 1 year. High rank = options expensive vs past year."""
    try:
        close = hist["Close"]
        if len(close) < 60:
            return None
        log_ret   = close.pct_change().dropna()
        hv_series = (log_ret.rolling(30).std() * (252 ** 0.5) * 100).dropna()
        if len(hv_series) < 2:
            return None
        cur = float(hv_series.iloc[-1])
        lo  = float(hv_series.min())
        hi  = float(hv_series.max())
        if hi == lo:
            return 50.0
        return round((cur - lo) / (hi - lo) * 100, 1)
    except Exception:
        return None


def _get_days_to_earnings(symbol: str) -> Optional[int]:
    """Days until next confirmed earnings date, or None if unknown."""
    try:
        dates = yf.Ticker(symbol).get_earnings_dates(limit=8)
        if dates is None or dates.empty:
            return None
        today = date.today()
        for dt in sorted(dates.index):
            d     = dt.date() if hasattr(dt, "date") else dt
            delta = (d - today).days
            if delta >= 0:
                return delta
        return None
    except Exception:
        return None


def get_daily_signal(symbol: str) -> dict:
    """Fetch 1-year daily OHLCV and compute RSI + SMA + volume + HV rank + earnings signal."""
    try:
        hist = yf.Ticker(symbol).history(period="1y")
        if len(hist) < SMA_PERIOD + RSI_PERIOD:
            return {"signal": "NONE", "rsi": None, "sma50": None, "price": None,
                    "reason": "insufficient data"}

        close  = hist["Close"]
        volume = hist["Volume"]

        rsi   = _compute_rsi(close, RSI_PERIOD)
        sma50 = float(close.rolling(SMA_PERIOD).mean().iloc[-1])
        price = float(close.iloc[-1])

        # Volume confirmation: current day vs 20-day average
        avg_vol_20    = float(volume.rolling(20).mean().iloc[-1])
        current_vol   = float(volume.iloc[-1])
        vol_above_avg = current_vol > avg_vol_20

        # Near SMA50: price is within 5% above SMA50 (not already extended)
        near_sma50 = price <= sma50 * 1.05

        # IV rank proxy via 30-day rolling HV over 1 year
        hv_rank = _get_hv_rank_from_hist(hist)

        # Days to next earnings (called separately to avoid slowing hist fetch)
        days_to_earnings = _get_days_to_earnings(symbol)

        # ── Signal logic ───────────────────────────────────────────────────────
        if rsi > RSI_SELL_THRESHOLD:
            signal = "SELL"
            reason = f"RSI={rsi:.1f} overbought"

        elif rsi < RSI_BUY_THRESHOLD and (
            not settings.STRATEGY_REQUIRE_UPTREND_FILTER
            or (price > sma50 and (near_sma50 or vol_above_avg))
        ):
            # Additional gatekeepers before opening a new position. The trend
            # filter (price > SMA50 + near-SMA or above-avg-volume) was the
            # safety against catching falling knives; STRATEGY_REQUIRE_UPTREND_FILTER
            # lets the operator opt into trading oversold downtrends. Other
            # gates (regime / AI / risk) still run downstream.
            if hv_rank is not None and hv_rank > IV_RANK_MAX:
                signal = "NONE"
                reason = f"HV rank {hv_rank:.0f}% > {IV_RANK_MAX}% — options expensive"
            elif days_to_earnings is not None and days_to_earnings <= EARNINGS_DAYS_MIN:
                signal = "NONE"
                reason = f"earnings in {days_to_earnings}d — skipping"
            else:
                signal = "BUY"
                reason = f"RSI={rsi:.1f}, SMA50={sma50:.2f}, price={price:.2f}"

        else:
            signal = "NONE"
            reason = f"RSI={rsi:.1f}, SMA50={sma50:.2f}, price={price:.2f}"

        return {
            "signal":           signal,
            "rsi":              round(rsi, 1),
            "sma50":            round(sma50, 2),
            "price":            round(price, 2),
            "vol_above_avg":    vol_above_avg,
            "near_sma50":       near_sma50,
            "hv_rank":          hv_rank,
            "days_to_earnings": days_to_earnings,
            "reason":           reason,
        }
    except Exception as e:
        logger.error(f"Signal error for {symbol}: {e}")
        return {"signal": "NONE", "rsi": None, "sma50": None, "price": None, "reason": str(e)}


def _days_to_expiry(occ_symbol: str) -> Optional[int]:
    raw = occ_symbol[2:] if occ_symbol.startswith("O:") else occ_symbol
    m   = re.match(r"^[A-Z]+(\d{6})[CP]\d+$", raw)
    if not m:
        return None
    try:
        return (datetime.strptime(m.group(1), "%y%m%d").date() - date.today()).days
    except ValueError:
        return None


def _parse_underlying(occ_symbol: str) -> Optional[str]:
    raw = occ_symbol[2:] if occ_symbol.startswith("O:") else occ_symbol
    m   = re.match(r"^([A-Z]+)\d{6}[CP]\d+$", raw)
    return m.group(1) if m else None


# ── Strategy class ─────────────────────────────────────────────────────────────

class DailyRSIStrategy:
    def __init__(self):
        self.positions:          Dict[str, int]   = defaultdict(int)
        self.option_symbols:     Dict[str, str]   = {}   # stock → OCC call symbol held
        self.entry_opt_prices:   Dict[str, float] = {}   # stock → option price at entry
        self.entry_stock_prices: Dict[str, float] = {}   # stock → stock price at entry
        self.peak_opt_prices:    Dict[str, float] = {}   # stock → highest mark seen
        self.signals:            Dict[str, dict]  = {}   # stock → latest signal dict
        self.last_signal_date:   Optional[date]   = None

    def active_position_count(self) -> int:
        return sum(1 for q in self.positions.values() if q > 0)

    def has_capacity(self) -> bool:
        return self.active_position_count() < MAX_POSITIONS

    def has_position(self, symbol: str) -> bool:
        return self.positions[symbol] > 0

    def _audit(self, symbol: str, side: str, qty: int, stock_price: float,
               status: str, detail: str = "", option_symbol: str = ""):
        record_audit_entry({
            "symbol":       option_symbol or symbol,
            "side":         side,
            "quantity":     qty,
            "order_type":   "LMT",
            "price":        stock_price,
            "status":       status,
            "detail":       detail,
            "source":       "rsi_strategy",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        })

    def execute_buy(self, symbol: str, stock_price: float, call_symbol: str) -> bool:
        if not call_symbol:
            logger.warning(f"[BUY] No call symbol for {symbol} — skipping")
            self._audit(symbol, "BUY", TRADE_QUANTITY, stock_price, "failed", "no call symbol")
            return False
        try:
            result = alpaca_client.place_order(
                symbol=call_symbol, quantity=TRADE_QUANTITY, side="BUY", order_type="MKT"
            )
            if result:
                entry_opt = float(result.get("limit_price") or 0)
                self.positions[symbol]          += TRADE_QUANTITY
                self.option_symbols[symbol]      = call_symbol
                self.entry_opt_prices[symbol]    = entry_opt
                self.entry_stock_prices[symbol]  = stock_price
                self.peak_opt_prices[symbol]     = entry_opt
                logger.info(
                    f"[BUY] {call_symbol} for {symbol} | stock ${stock_price:.2f} | "
                    f"option entry ${entry_opt:.2f}"
                )
                self._audit(symbol, "BUY", TRADE_QUANTITY, stock_price, "submitted",
                            option_symbol=call_symbol)
                return True
            logger.error(f"[ERROR] Alpaca rejected buy for {symbol} ({call_symbol})")
            self._audit(symbol, "BUY", TRADE_QUANTITY, stock_price, "failed",
                        "Alpaca rejected", call_symbol)
            return False
        except Exception as e:
            logger.error(f"[ERROR] Buy error for {symbol}: {e}")
            self._audit(symbol, "BUY", TRADE_QUANTITY, stock_price, "error", str(e))
            return False

    def execute_sell(self, symbol: str, stock_price: float, reason: str = "") -> bool:
        call_symbol = self.option_symbols.get(symbol)
        if not call_symbol:
            self.positions[symbol] = 0
            return False
        qty = min(self.positions[symbol], TRADE_QUANTITY)
        if qty <= 0:
            return False
        try:
            result = alpaca_client.place_order(
                symbol=call_symbol, quantity=qty, side="SELL", order_type="MKT"
            )
            if result:
                self.positions[symbol] -= qty
                if self.positions[symbol] <= 0:
                    self.option_symbols.pop(symbol, None)
                    self.entry_opt_prices.pop(symbol, None)
                    self.entry_stock_prices.pop(symbol, None)
                    self.peak_opt_prices.pop(symbol, None)
                logger.info(f"[SELL] {call_symbol} for {symbol} | {reason}")
                self._audit(symbol, "SELL", qty, stock_price, "submitted",
                            detail=reason, option_symbol=call_symbol)
                return True
            logger.error(f"[ERROR] Alpaca rejected sell for {symbol} ({call_symbol})")
            self._audit(symbol, "SELL", qty, stock_price, "failed",
                        "Alpaca rejected", call_symbol)
            return False
        except Exception as e:
            logger.error(f"[ERROR] Sell error for {symbol}: {e}")
            self._audit(symbol, "SELL", qty, stock_price, "error", str(e), call_symbol)
            return False

    def get_status(self) -> dict:
        return {
            "positions":          dict(self.positions),
            "option_symbols":     dict(self.option_symbols),
            "entry_opt_prices":   dict(self.entry_opt_prices),
            "entry_stock_prices": dict(self.entry_stock_prices),
            "peak_opt_prices":    dict(self.peak_opt_prices),
            "signals":            dict(self.signals),
            "last_signal_date":   str(self.last_signal_date) if self.last_signal_date else None,
        }


rsi_strategy = DailyRSIStrategy()
momentum_strategy = rsi_strategy  # backward-compatible alias used by routes


# ── Main loop ──────────────────────────────────────────────────────────────────

async def start_trading_loop():
    from app.services.watchlist_manager import get_atm_option_symbols

    logger.info("[START] Daily RSI strategy — RSI<35 + above SMA50 → buy ATM call")
    logger.info(
        f"[INFO] Buy RSI<{RSI_BUY_THRESHOLD} | Sell RSI>{RSI_SELL_THRESHOLD} | "
        f"SMA{SMA_PERIOD} | Profit +{PROFIT_TARGET:.0%} | Stop -{STOP_LOSS:.0%} | "
        f"Max {MAX_POSITIONS} positions | IV rank cap {IV_RANK_MAX}% | "
        f"Earnings buffer {EARNINGS_DAYS_MIN}d | "
        f"Trail activates at +{TRAILING_STOP_ACTIVATION:.0%} → -{TRAILING_STOP_PCT:.0%} floor"
    )

    alpaca_client.login()

    # Sync any existing option positions from Alpaca on startup
    live = alpaca_client.get_positions_pnl()
    if live:
        synced = 0
        for pos in live:
            sym         = pos["symbol"]
            qty         = int(float(pos["qty"]))
            asset_class = pos.get("asset_class", "")
            if qty <= 0 or asset_class != "us_option":
                continue
            underlying = _parse_underlying(sym)
            if underlying:
                rsi_strategy.positions[underlying]      = qty
                rsi_strategy.option_symbols[underlying] = f"O:{sym}"
                synced += 1
        logger.info(f"[STARTUP] Synced {synced} option position(s) from Alpaca")
    else:
        logger.info("[STARTUP] No open positions to sync")

    while True:
        try:
            today = date.today()

            # ── Once-per-day: compute RSI signals and open new positions ───────
            if rsi_strategy.last_signal_date != today:
                symbols = list(WATCHLIST)
                logger.info(f"[SIGNAL] Computing daily signals for {len(symbols)} stocks...")

                raw_signals = await asyncio.gather(
                    *[asyncio.to_thread(get_daily_signal, sym) for sym in symbols],
                    return_exceptions=True,
                )

                # Separate SELL triggers from BUY candidates
                buy_candidates: List[tuple] = []

                for sym, sig in zip(symbols, raw_signals):
                    if isinstance(sig, Exception):
                        logger.error(f"[SIGNAL] {sym}: {sig}")
                        continue

                    rsi_strategy.signals[sym] = sig
                    logger.info(
                        f"[SIGNAL] {sym}: {sig['signal']} | "
                        f"RSI={sig['rsi']} | SMA50={sig['sma50']} | "
                        f"price=${sig['price']} | HV_rank={sig.get('hv_rank')} | "
                        f"earnings_in={sig.get('days_to_earnings')}d | "
                        f"near_sma50={sig.get('near_sma50')} | "
                        f"vol_above_avg={sig.get('vol_above_avg')}"
                    )

                    if sig["signal"] == "BUY" and not rsi_strategy.has_position(sym):
                        buy_candidates.append((sym, sig))

                    elif sig["signal"] == "SELL" and rsi_strategy.has_position(sym):
                        sma50_dist = (
                            round((sig["price"] - sig["sma50"]) / sig["sma50"] * 100, 1)
                            if sig.get("sma50") else None
                        )
                        decision = await asyncio.to_thread(
                            ai_advisor.decide,
                            sym, sig["price"] or 0, 0,
                            [], rsi_strategy.positions[sym], "SELL",
                            {
                                "rsi":              sig["rsi"],
                                "sma50_dist_pct":   sma50_dist,
                                "hv_rank":          sig.get("hv_rank"),
                                "days_to_earnings": sig.get("days_to_earnings"),
                            },
                        )
                        if decision["action"] == "SELL" and decision["confidence"] >= MIN_CONFIDENCE:
                            risk = await asyncio.to_thread(
                                risk_gate.evaluate, sym, "SELL", 0.0
                            )
                            if risk.approved:
                                rsi_strategy.execute_sell(sym, sig["price"] or 0, "RSI overbought")
                            else:
                                logger.warning(f"[RISK] SELL blocked for {sym} — {risk.reason}")
                        else:
                            logger.info(f"[AI] SELL filtered for {sym} — {decision['reason']}")

                # Sort BUY candidates by RSI ascending (most oversold = highest priority)
                buy_candidates.sort(key=lambda x: x[1].get("rsi") or 100)

                # Regime gate — global skip when the macro environment is adverse.
                # Runs once per pass; per-symbol AI + risk still apply for survivors.
                regime_decision = await asyncio.to_thread(regime_gate.classify)
                if buy_candidates and not regime_decision.allow_new_buys:
                    logger.warning(
                        f"[REGIME] BUY phase skipped — {regime_decision.regime}: "
                        f"{regime_decision.reason}"
                    )
                    buy_candidates = []

                for sym, sig in buy_candidates:
                    if not rsi_strategy.has_capacity():
                        logger.info(
                            f"[CAPACITY] {rsi_strategy.active_position_count()}/{MAX_POSITIONS} "
                            f"positions filled — skipping {sym} (RSI={sig['rsi']})"
                        )
                        break

                    sma50_dist = (
                        round((sig["price"] - sig["sma50"]) / sig["sma50"] * 100, 1)
                        if sig.get("sma50") else None
                    )
                    decision = await asyncio.to_thread(
                        ai_advisor.decide,
                        sym, sig["price"] or 0, 0, [], 0, "BUY",
                        {
                            "rsi":              sig["rsi"],
                            "sma50_dist_pct":   sma50_dist,
                            "hv_rank":          sig.get("hv_rank"),
                            "days_to_earnings": sig.get("days_to_earnings"),
                            "near_sma50":       sig.get("near_sma50"),
                            "vol_above_avg":    sig.get("vol_above_avg"),
                        },
                    )
                    if decision["action"] == "BUY" and decision["confidence"] >= MIN_CONFIDENCE:
                        stock_price = sig["price"] or 0
                        notional = _estimate_option_notional(stock_price, TRADE_QUANTITY)
                        risk = await asyncio.to_thread(
                            risk_gate.evaluate, sym, "BUY", notional
                        )
                        if not risk.approved:
                            logger.warning(f"[RISK] BUY blocked for {sym} — {risk.reason}")
                            continue

                        try:
                            opts     = await asyncio.to_thread(
                                get_atm_option_symbols, sym, OPTION_WEEKS_OUT
                            )
                            call_sym = opts[0] if opts else None
                        except Exception as e:
                            logger.warning(f"[BUY] Option lookup failed for {sym}: {e}")
                            call_sym = None
                        rsi_strategy.execute_buy(sym, stock_price, call_sym)
                    else:
                        logger.info(f"[AI] BUY filtered for {sym} — {decision['reason']}")

                rsi_strategy.last_signal_date = today

            # ── Every 60 s: monitor positions for stop/target/expiry/trailing ─
            if rsi_strategy.option_symbols:
                opt_syms = list(rsi_strategy.option_symbols.values())
                quotes   = await asyncio.to_thread(alpaca_client.get_batch_quotes, opt_syms)

                for stock_sym, opt_sym in list(rsi_strategy.option_symbols.items()):
                    q    = (quotes.get(opt_sym) or {})
                    bid  = float(q.get("bid") or 0)
                    ask  = float(q.get("ask") or 0)
                    mark = round((bid + ask) / 2, 2) if bid and ask else (bid or ask)

                    entry     = rsi_strategy.entry_opt_prices.get(stock_sym, 0)
                    days_left = _days_to_expiry(opt_sym)
                    reason    = None

                    if entry and mark:
                        gain_pct = (mark - entry) / entry

                        # Update running peak for trailing stop
                        prev_peak = rsi_strategy.peak_opt_prices.get(stock_sym, entry)
                        if mark > prev_peak:
                            rsi_strategy.peak_opt_prices[stock_sym] = mark
                            prev_peak = mark

                        # Fixed profit / stop targets
                        if gain_pct >= PROFIT_TARGET:
                            reason = f"profit target +{gain_pct:.1%}"
                        elif gain_pct <= -STOP_LOSS:
                            reason = f"stop loss {gain_pct:.1%}"
                        else:
                            # Trailing stop: activate only after TRAILING_STOP_ACTIVATION gain
                            peak_gain = (prev_peak - entry) / entry
                            if peak_gain >= TRAILING_STOP_ACTIVATION:
                                trail_floor = prev_peak * (1 - TRAILING_STOP_PCT)
                                if mark <= trail_floor:
                                    reason = (
                                        f"trailing stop: mark ${mark:.2f} ≤ "
                                        f"floor ${trail_floor:.2f} "
                                        f"(peak ${prev_peak:.2f})"
                                    )

                    if days_left is not None and days_left <= DAYS_BEFORE_EXPIRY:
                        reason = reason or f"expiry in {days_left} day(s)"

                    if reason:
                        logger.info(f"[EXIT] {opt_sym} ({stock_sym}): {reason}")
                        stock_px = rsi_strategy.entry_stock_prices.get(stock_sym, 0)
                        rsi_strategy.execute_sell(stock_sym, stock_px, reason)
                    elif mark and entry:
                        gain_pct  = (mark - entry) / entry
                        prev_peak = rsi_strategy.peak_opt_prices.get(stock_sym, entry)
                        peak_gain = (prev_peak - entry) / entry
                        logger.info(
                            f"[HOLD] {opt_sym} | entry ${entry:.2f} → mark ${mark:.2f} "
                            f"({gain_pct:+.1%}) | peak ${prev_peak:.2f} ({peak_gain:+.1%}) | "
                            f"{days_left}d to expiry"
                        )

            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Trading loop error: {e}")
            await asyncio.sleep(60)
