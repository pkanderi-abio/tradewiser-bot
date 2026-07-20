# app/services/alpaca_client.py — Alpaca Markets trading client

import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    GetOrdersRequest,
    GetOptionContractsRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, ContractType, AssetStatus, PositionIntent, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, OptionChainRequest, OptionLatestQuoteRequest, OptionSnapshotRequest
from app.core.config import settings
import yfinance as yf

logger = logging.getLogger(__name__)

class AlpacaClient:
    """Alpaca trading client - supports stocks and options.

    Authentication failures used to latch _auth_failed permanently, which meant
    a single startup blip silently disabled trading for the whole process
    lifetime (we hit this on 2026-05-12: the bot ran for 27 days unable to
    talk to the broker). Auth now uses a cooldown window — we still avoid
    hammering Alpaca after a failure, but a transient failure no longer
    requires a service restart to recover. The cooldown is
    settings.ALPACA_AUTH_RETRY_COOLDOWN_SECONDS (default 60).
    """

    def __init__(self):
        self.trading_client = TradingClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY,
            paper="paper" in settings.ALPACA_BASE_URL
        )
        self.stock_data_client = StockHistoricalDataClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY
        )
        self.options_data_client = OptionHistoricalDataClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY
        )
        self.authenticated = False
        self._auth_failed = False              # latched within the cooldown window only
        self._account_id: Optional[str] = None
        self._last_auth_attempt: float = 0.0   # epoch; 0 = never attempted
        self._last_auth_error: Optional[str] = None
        self._consecutive_auth_failures: int = 0
        self._last_auth_success: Optional[float] = None
        # Captures the last broker error from place_order so callers can persist
        # the real reason instead of writing the opaque "Alpaca rejected" string.
        self.last_order_error: Optional[str] = None
        # Small TTL cache for /v2/clock — the trading loop asks every tick and
        # Alpaca's clock only changes at open/close boundaries.
        # Tuple of (fetched_at_epoch, clock_dict_or_None).
        self._clock_cache: Optional[tuple] = None

    def login(self) -> bool:
        """Alpaca doesn't require explicit login - authentication is via API keys"""
        return self._ensure_authenticated()

    def _ensure_authenticated(self) -> bool:
        if self.authenticated:
            return True

        # Auth failed recently? Don't hammer — wait out the cooldown.
        if self._auth_failed:
            elapsed = time.time() - self._last_auth_attempt
            cooldown = settings.ALPACA_AUTH_RETRY_COOLDOWN_SECONDS
            if elapsed < cooldown:
                return False
            # Cooldown elapsed — clear the latch and fall through to retry.
            logger.info(
                f"Alpaca auth cooldown elapsed ({elapsed:.0f}s >= {cooldown}s) — retrying auth "
                f"(consecutive failures so far: {self._consecutive_auth_failures})"
            )
            self._auth_failed = False

        self._last_auth_attempt = time.time()
        try:
            account = self.trading_client.get_account()
            if account:
                self.authenticated = True
                self._account_id = account.id
                self._last_auth_error = None
                self._consecutive_auth_failures = 0
                self._last_auth_success = self._last_auth_attempt
                logger.info("Alpaca authentication successful.")
                return True
            logger.error("Alpaca authentication failed - no account info")
            self._auth_failed = True
            self._last_auth_error = "no account info returned"
            self._consecutive_auth_failures += 1
            return False
        except Exception as e:
            logger.error(f"Alpaca authentication failed: {e}")
            self._auth_failed = True
            self._last_auth_error = f"{type(e).__name__}: {str(e)[:160]}"
            self._consecutive_auth_failures += 1
            return False

    def broker_snapshot(self) -> Dict[str, Any]:
        """State for the /health/broker endpoint.

        Surfaces the auth state, last error, cooldown progress, and how long
        ago the last successful authentication happened. This is the
        thing operators look at when 'why no trades' comes up — without
        this, the only signal was an empty audit log, which is the same
        appearance whether the bot is healthy and quiet or broken and silent.
        """
        now = time.time()
        cooldown = settings.ALPACA_AUTH_RETRY_COOLDOWN_SECONDS
        seconds_since_attempt = (now - self._last_auth_attempt) if self._last_auth_attempt else None
        seconds_until_retry = None
        if self._auth_failed and seconds_since_attempt is not None:
            seconds_until_retry = max(0.0, cooldown - seconds_since_attempt)
        return {
            "authenticated": self.authenticated,
            "auth_failed_latched": self._auth_failed,
            "consecutive_failures": self._consecutive_auth_failures,
            "last_error": self._last_auth_error,
            "last_attempt_age_seconds": (
                round(seconds_since_attempt, 1) if seconds_since_attempt is not None else None
            ),
            "last_success_age_seconds": (
                round(now - self._last_auth_success, 1)
                if self._last_auth_success is not None else None
            ),
            "retry_cooldown_seconds": cooldown,
            "seconds_until_retry": (
                round(seconds_until_retry, 1) if seconds_until_retry is not None else None
            ),
            "base_url": settings.ALPACA_BASE_URL,
            "paper_mode": "paper" in settings.ALPACA_BASE_URL,
            "account_id": self._account_id,
        }

    def get_quote(self, symbol: str):
        """Get quote for stock or option symbol"""
        if not self._ensure_authenticated():
            logger.debug("Alpaca authentication failed, using yfinance fallback for quotes")
            return self._get_quote_fallback(symbol)

        # Check if it's an options symbol (starts with 'O:')
        if symbol.startswith('O:'):
            return self._get_options_quote(symbol)
        else:
            return self._get_stock_quote(symbol)

    def get_batch_quotes(self, symbols: list) -> dict:
        """Fetch quotes for a list of symbols in as few API calls as possible.
        Returns {symbol: quote_dict}. Options (O: prefix) and stocks are batched separately.
        """
        if not self._ensure_authenticated():
            return {}

        stock_syms  = [s for s in symbols if not s.startswith("O:")]
        option_syms = [s for s in symbols if s.startswith("O:")]
        results: dict = {}

        # --- stocks: single batched call ---
        if stock_syms:
            try:
                req    = StockLatestQuoteRequest(symbol_or_symbols=stock_syms)
                quotes = self.stock_data_client.get_stock_latest_quote(req)
                for sym in stock_syms:
                    if sym in quotes:
                        q = quotes[sym]
                        results[sym] = {
                            "symbol": sym,
                            "pLast": q.ask_price or q.bid_price,
                            "bid": q.bid_price,
                            "ask": q.ask_price,
                            "source": "alpaca",
                            "asset_class": "stock",
                        }
            except Exception as e:
                logger.error(f"Batch stock quote error: {e}")
                # fall back individually
                for sym in stock_syms:
                    q = self._get_quote_fallback(sym)
                    if q:
                        results[sym] = q

        # --- options: single batched call ---
        if option_syms:
            try:
                raw_syms = [s[2:] for s in option_syms]   # strip "O:" prefix
                req      = OptionLatestQuoteRequest(symbol_or_symbols=raw_syms)
                quotes   = self.options_data_client.get_option_latest_quote(req)
                for orig, raw in zip(option_syms, raw_syms):
                    if raw in quotes:
                        q = quotes[raw]
                        results[orig] = {
                            "symbol": orig,
                            "pLast": q.ask_price or q.bid_price,
                            "bid": q.bid_price,
                            "ask": q.ask_price,
                            "source": "alpaca",
                            "asset_class": "option",
                        }
            except Exception as e:
                logger.debug(f"Batch options quote error (may be outside market hours): {e}")

        return results

    def _get_stock_quote(self, symbol: str):
        """Get stock quote"""
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self.stock_data_client.get_stock_latest_quote(request)
            if quotes and symbol in quotes:
                quote = quotes[symbol]
                return {
                    'symbol': symbol,
                    'pLast': quote.ask_price or quote.bid_price,
                    'bid': quote.bid_price,
                    'ask': quote.ask_price,
                    'volume': quote.ask_size + quote.bid_size if quote.ask_size and quote.bid_size else None,
                    'source': 'alpaca',
                    'asset_class': 'stock'
                }
            else:
                logger.warning(f"Alpaca returned empty stock quote for {symbol}, using fallback")
                return self._get_quote_fallback(symbol)
        except Exception as e:
            logger.error(f"Alpaca stock quote fetch error for {symbol}: {e}, using fallback")
            return self._get_quote_fallback(symbol)

    def _get_options_quote(self, symbol: str):
        """Get options quote"""
        try:
            # Remove 'O:' prefix for Alpaca
            option_symbol = symbol[2:] if symbol.startswith('O:') else symbol
            request = OptionLatestQuoteRequest(symbol_or_symbols=option_symbol)
            quotes = self.options_data_client.get_option_latest_quote(request)
            if quotes and option_symbol in quotes:
                quote = quotes[option_symbol]
                return {
                    'symbol': symbol,
                    'pLast': quote.ask_price or quote.bid_price,
                    'bid': quote.bid_price,
                    'ask': quote.ask_price,
                    'volume': quote.ask_size + quote.bid_size if quote.ask_size and quote.bid_size else None,
                    'source': 'alpaca',
                    'asset_class': 'option'
                }
            else:
                logger.warning(f"Alpaca returned empty options quote for {symbol}")
                return None
        except Exception as e:
            logger.error(f"Alpaca options quote fetch error for {symbol}: {e}")
            return None

    def _get_quote_fallback(self, symbol: str):
        """Fallback to yfinance for quotes"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return {
                'symbol': symbol,
                'pLast': info.get('currentPrice') or info.get('regularMarketPrice'),
                'bid': info.get('bid'),
                'ask': info.get('ask'),
                'volume': info.get('volume'),
                'source': 'yfinance_fallback'
            }
        except Exception as e:
            logger.error(f"yfinance fallback failed for {symbol}: {e}")
            return None

    def market_clock(self) -> Optional[Dict[str, Any]]:
        """Return {is_open, next_open, next_close, timestamp} or None on error.

        Cached for settings.MARKET_CLOCK_CACHE_SECONDS so the trading loop
        doesn't hit /v2/clock every 60 s. Fails open (returns None) on any
        auth or API error — the caller should treat None as "assume open"
        rather than halt trading, otherwise a broker outage silently stops
        the bot the same way the May-2026 27-day auth incident did.
        """
        now = time.time()
        ttl = max(0, int(settings.MARKET_CLOCK_CACHE_SECONDS))
        if self._clock_cache and ttl:
            fetched_at, cached = self._clock_cache
            if now - fetched_at < ttl:
                return cached
        if not self._ensure_authenticated():
            return None
        try:
            clock = self.trading_client.get_clock()
        except Exception as e:
            logger.warning(f"market_clock() failed: {e}")
            self._clock_cache = (now, None)
            return None
        payload = {
            "is_open": bool(getattr(clock, "is_open", False)),
            "next_open": getattr(clock, "next_open", None),
            "next_close": getattr(clock, "next_close", None),
            "timestamp": getattr(clock, "timestamp", None),
        }
        self._clock_cache = (now, payload)
        return payload

    def cancel_stale_open_orders(self, symbol: str, max_age_minutes: int) -> int:
        """Cancel stale open SELL orders for `symbol` and return the count.

        Only SELLs are reaped. The whole point is to unlock a position whose
        qty_available is pinned at 0 by an unfilled SELL — cancelling stale
        BUYs wouldn't help with that, and cancelling a BUY the strategy still
        wants to fill would be surprising (there's no layered-entry code
        today, but this method is a plausible target for one). If you ever
        need a BUY reaper too, make it a separate call so the intent stays
        explicit at the call site.

        Motivation: option orders are LIMIT-only (Alpaca doesn't accept MKT
        for options). A limit set at yesterday's mid can miss forever, and
        execute_sell defers new SELLs while any open order exists — so a
        stale unfilled SELL locks the position until an operator intervenes.
        Never raises — a broker failure returns 0 so the caller falls through
        to the existing defer path.
        """
        if max_age_minutes <= 0:
            return 0
        if not self._ensure_authenticated():
            return 0
        raw = symbol[2:] if symbol.startswith("O:") else symbol
        try:
            req = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[raw],
                side=OrderSide.SELL,
                limit=50,
            )
            open_orders = self.trading_client.get_orders(filter=req) or []
        except Exception as e:
            logger.warning(f"cancel_stale_open_orders({symbol}): list failed: {e}")
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        cancelled = 0
        for order in open_orders:
            # Defense-in-depth: the SDK filter above should already exclude
            # non-SELLs, but confirm before touching the order.
            side_val = order.side.value if hasattr(order.side, "value") else str(order.side)
            if str(side_val).lower() != "sell":
                continue
            submitted = getattr(order, "submitted_at", None) or getattr(order, "created_at", None)
            if submitted is None:
                continue
            # Alpaca returns tz-aware datetimes; compare in UTC.
            if submitted.tzinfo is None:
                submitted = submitted.replace(tzinfo=timezone.utc)
            if submitted > cutoff:
                continue  # still fresh
            try:
                self.trading_client.cancel_order_by_id(order.id)
                cancelled += 1
                age_min = (datetime.now(timezone.utc) - submitted).total_seconds() / 60
                logger.info(
                    f"cancel_stale_open_orders({symbol}): cancelled SELL order "
                    f"{order.id} submitted {age_min:.1f}min ago"
                )
            except Exception as e:
                logger.warning(
                    f"cancel_stale_open_orders({symbol}): failed to cancel {order.id}: {e}"
                )
        return cancelled

    def has_open_order(self, symbol: str) -> Optional[bool]:
        """Return True if any open order exists for `symbol` (with or without "O:" prefix).

        Used to gate the exit loop: a DAY-TIF sell submitted just after close
        stays in `accepted` until the next session opens, reserving qty_available
        to 0. Any further SELL gets a confusing "uncovered option contracts"
        rejection. Pre-checking with this saves the audit log from a 1-per-minute
        spam of rejections. Returns None on broker error so callers can decide
        whether to fail open or skip.
        """
        if not self._ensure_authenticated():
            return None
        raw = symbol[2:] if symbol.startswith("O:") else symbol
        try:
            req = GetOrdersRequest(
                status=QueryOrderStatus.OPEN,
                symbols=[raw],
                limit=10,
            )
            open_orders = self.trading_client.get_orders(filter=req)
            return bool(open_orders)
        except Exception as e:
            logger.warning(f"has_open_order({symbol}) check failed: {e}")
            return None

    def place_order(self, symbol: str, quantity: int, side: str, order_type: str = "MKT",
                   price: Optional[float] = None, enforce: str = "GTC",
                   outside_regular_trading_hour: bool = False,
                   stp_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        self.last_order_error = None
        if not self._ensure_authenticated():
            self.last_order_error = "not authenticated"
            logger.error("Unable to place order: not authenticated.")
            return None

        try:
            # Check if it's an options symbol
            if symbol.startswith('O:'):
                return self._place_options_order(symbol, quantity, side, order_type, price, enforce)
            else:
                return self._place_stock_order(symbol, quantity, side, order_type, price, enforce)

        except Exception as e:
            self.last_order_error = str(e)
            logger.error(f"Failed to place order: {e}")
            return None

    def _place_stock_order(self, symbol: str, quantity: int, side: str, order_type: str = "MKT",
                          price: Optional[float] = None, enforce: str = "GTC") -> Optional[Dict[str, Any]]:
        """Place stock order"""
        alpaca_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        alpaca_type = OrderType.MARKET if order_type == "MKT" else OrderType.LIMIT
        time_in_force = TimeInForce.GTC if enforce == "GTC" else TimeInForce.DAY

        order_request = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=quantity,
            side=alpaca_side,
            type=alpaca_type,
            time_in_force=time_in_force
        )

        # Add limit price if specified
        if order_type == "LMT" and price:
            order_request.limit_price = price

        order = self.trading_client.submit_order(order_request)
        logger.info(f"Stock order placed: {order.id} for {quantity} {symbol} {side}")

        return {
            'order_id': order.id,
            'symbol': order.symbol,
            'quantity': order.qty,
            'side': order.side.value,
            'type': order.type.value,
            'status': order.status.value,
            'asset_class': 'stock'
        }

    def _place_options_order(self, symbol: str, quantity: int, side: str, order_type: str = "MKT",
                            price: Optional[float] = None, enforce: str = "GTC") -> Optional[Dict[str, Any]]:
        """Place options limit order. Alpaca does not support market orders for options."""
        try:
            option_symbol = symbol[2:] if symbol.startswith('O:') else symbol
            alpaca_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL

            # Determine limit price: use provided price, or fetch live bid/ask
            limit_price = price
            if limit_price is None:
                quote = self._get_options_quote(symbol)
                if quote:
                    bid = float(quote.get("bid") or 0)
                    ask = float(quote.get("ask") or 0)
                    last = float(quote.get("pLast") or 0)
                    if side.upper() == "BUY":
                        # Mid-price halves the spread cost vs buying at full ask
                        if bid > 0 and ask > 0:
                            limit_price = round((bid + ask) / 2, 2)
                        else:
                            limit_price = ask or last
                    else:
                        # For sells: use bid, fall back to mid or last, minimum $0.01
                        if bid > 0 and ask > 0:
                            limit_price = round((bid + ask) / 2, 2)
                        else:
                            limit_price = bid or ask or last or 0.01

            if not limit_price or float(limit_price) <= 0:
                self.last_order_error = "no valid options quote available"
                logger.error(f"Cannot place options order for {symbol}: no valid price available")
                return None

            limit_price = max(round(float(limit_price), 2), 0.01)  # Alpaca minimum

            # Without an explicit position_intent, Alpaca's risk engine treats
            # the SELL as sell_to_open (writing a naked call) and rejects with
            # "account not eligible to trade uncovered option contracts" because
            # the paper account doesn't carry the level for naked writing. The
            # strategy only ever takes long calls, so BUY = open, SELL = close.
            intent = (
                PositionIntent.BUY_TO_OPEN
                if alpaca_side == OrderSide.BUY
                else PositionIntent.SELL_TO_CLOSE
            )
            order_request = LimitOrderRequest(
                symbol=option_symbol,
                qty=quantity,
                side=alpaca_side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
                position_intent=intent,
            )

            order = self.trading_client.submit_order(order_request)
            logger.info(f"Options limit order: {order.id} | {quantity} {symbol} {side} @ ${limit_price}")

            return {
                'order_id': order.id,
                'symbol': symbol,
                'quantity': order.qty,
                'side': order.side.value,
                'type': order.type.value,
                'status': order.status.value,
                'limit_price': limit_price,
                'asset_class': 'option'
            }

        except Exception as e:
            self.last_order_error = str(e)
            logger.error(f"Failed to place options order for {symbol}: {e}")
            return None

    def get_options_chain(self, underlying_symbol: str, expiration_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get options chain for an underlying symbol"""
        if not self._ensure_authenticated():
            logger.error("Unable to get options chain: not authenticated.")
            return None

        try:
            request = OptionChainRequest(
                underlying_symbol=underlying_symbol.upper(),
                expiration_date=expiration_date
            )
            chain = self.options_data_client.get_option_chain(request)
            return {
                'underlying_symbol': underlying_symbol,
                'chains': chain,
                'source': 'alpaca'
            }
        except Exception as e:
            logger.error(f"Failed to get options chain for {underlying_symbol}: {e}")
            return None

    def get_account_pnl(self):
        """Return account-level P&L summary from Alpaca."""
        if not self._ensure_authenticated():
            return None
        try:
            acct = self.trading_client.get_account()

            def _f(val, default=0.0):
                return float(val) if val is not None else default

            equity      = _f(acct.equity)
            last_equity = _f(acct.last_equity)
            day_pl      = equity - last_equity
            day_plpc    = (day_pl / last_equity * 100) if last_equity else 0.0
            return {
                "equity":          equity,
                "cash":            _f(acct.cash),
                "portfolio_value": _f(acct.portfolio_value),
                "buying_power":    _f(acct.buying_power),
                "unrealized_pl":   _f(getattr(acct, "unrealized_pl",   None)),
                "unrealized_plpc": _f(getattr(acct, "unrealized_plpc", None)),
                "last_equity":     last_equity,
                "day_pl":          round(day_pl, 2),
                "day_plpc":        round(day_plpc, 4),
            }
        except Exception as e:
            logger.error(f"Failed to fetch account P&L: {e}")
            return None

    def list_option_contracts(self, underlying: str, expiry, opt_type: str = "call",
                              strike_min: Optional[float] = None,
                              strike_max: Optional[float] = None):
        """Return active listed option contracts for an underlying + expiry.

        Used by the watchlist's ATM picker to snap to a real listed strike
        instead of computing one heuristically — e.g. AMZN at ~$238 has $5
        strike spacing on weeklies, not $1, and the hardcoded picker was
        generating contracts that don't exist and getting 404s from Alpaca.
        """
        if not self._ensure_authenticated():
            return None
        try:
            ctype = ContractType.CALL if opt_type.lower() == "call" else ContractType.PUT
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying.upper()],
                status=AssetStatus.ACTIVE,
                expiration_date=expiry,
                type=ctype,
                strike_price_gte=str(strike_min) if strike_min is not None else None,
                strike_price_lte=str(strike_max) if strike_max is not None else None,
                limit=200,
            )
            resp = self.trading_client.get_option_contracts(req)
            contracts = getattr(resp, "option_contracts", None)
            if contracts is None and isinstance(resp, dict):
                contracts = resp.get("option_contracts", [])
            return [{
                "symbol":          c.symbol,
                "strike_price":    float(c.strike_price),
                "expiration_date": str(c.expiration_date),
                "type":            c.type.value if hasattr(c.type, "value") else str(c.type),
                "tradable":        bool(getattr(c, "tradable", True)),
            } for c in (contracts or [])]
        except Exception as e:
            logger.error(f"Failed to list option contracts for {underlying} {expiry}: {e}")
            return None

    def get_positions_pnl(self):
        """Return per-symbol positions with unrealized P&L from Alpaca."""
        if not self._ensure_authenticated():
            return None
        try:
            positions = self.trading_client.get_all_positions()
            return [{
                "symbol":                   p.symbol,
                "qty":                      float(p.qty),
                "side":                     p.side.value,
                "asset_class":              p.asset_class.value,
                "avg_entry_price":          float(p.avg_entry_price),
                "current_price":            float(p.current_price)            if p.current_price            else None,
                "market_value":             float(p.market_value)             if p.market_value             else None,
                "cost_basis":               float(p.cost_basis)               if p.cost_basis               else None,
                "unrealized_pl":            float(p.unrealized_pl)            if p.unrealized_pl            else 0.0,
                "unrealized_plpc":          float(p.unrealized_plpc)          if p.unrealized_plpc          else 0.0,
                "unrealized_intraday_pl":   float(p.unrealized_intraday_pl)   if p.unrealized_intraday_pl   else 0.0,
                "unrealized_intraday_plpc": float(p.unrealized_intraday_plpc) if p.unrealized_intraday_plpc else 0.0,
            } for p in positions]
        except Exception as e:
            logger.error(f"Failed to fetch positions P&L: {e}")
            return None

    def get_current_orders(self):
        if not self._ensure_authenticated():
            logger.error("Unable to fetch current orders: not authenticated.")
            return None

        try:
            request = GetOrdersRequest(status="open")
            orders = self.trading_client.get_orders(request)
            return [{
                'order_id': order.id,
                'symbol': order.symbol,
                'quantity': order.qty,
                'side': order.side.value,
                'type': order.type.value,
                'status': order.status.value,
                'submitted_at': order.submitted_at.isoformat() if order.submitted_at else None
            } for order in orders]
        except Exception as e:
            logger.error(f"Failed to fetch current orders: {e}")
            return None

    def get_order_history(self, limit: int = 50):
        if not self._ensure_authenticated():
            logger.error("Unable to fetch order history: not authenticated.")
            return None

        try:
            request = GetOrdersRequest(limit=limit)
            orders = self.trading_client.get_orders(request)
            return [{
                'order_id': order.id,
                'symbol': order.symbol,
                'quantity': order.qty,
                'side': order.side.value,
                'type': order.type.value,
                'status': order.status.value,
                'filled_qty': order.filled_qty,
                'submitted_at': order.submitted_at.isoformat() if order.submitted_at else None,
                'filled_at': order.filled_at.isoformat() if order.filled_at else None
            } for order in orders]
        except Exception as e:
            logger.error(f"Failed to fetch order history: {e}")
            return None

alpaca_client = AlpacaClient()
