# app/services/webull_client.py
# Legacy file - now using Alpaca for trading
# This file is kept for backward compatibility but uses Alpaca internally

from typing import Optional, Dict, Any
import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest, OptionLegRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, AssetClass
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, OptionChainRequest, OptionLatestQuoteRequest, OptionSnapshotRequest
from app.core.config import settings
import yfinance as yf

logger = logging.getLogger(__name__)

class AlpacaClient:
    """Alpaca trading client - supports stocks and options"""

    def __init__(self):
        self.trading_client = TradingClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY,
            paper=True if "paper" in settings.ALPACA_BASE_URL else False
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
        self._account_id: Optional[str] = None
        self._login_attempted = False

    def login(self) -> bool:
        """Alpaca doesn't require explicit login - authentication is via API keys"""
        if self.authenticated:
            return True

        if self._login_attempted:
            return False

        self._login_attempted = True

        try:
            # Test authentication by getting account info
            account = self.trading_client.get_account()
            if account:
                self.authenticated = True
                self._account_id = account.id
                logger.info("✅ Alpaca authentication successful.")
                return True
            else:
                logger.error("❌ Alpaca authentication failed - no account info")
                self.authenticated = False
                return False
        except Exception as e:
            logger.error(f"❌ Alpaca authentication failed: {e}")
            self.authenticated = False
            return False

    def get_quote(self, symbol: str):
        """Get quote for stock or option symbol"""
        if not self.authenticated:
            if not self._login_attempted:
                self.login()
            if not self.authenticated:
                logger.debug("Alpaca authentication failed, using yfinance fallback for quotes")
                return self._get_quote_fallback(symbol)

        # Check if it's an options symbol (starts with 'O:')
        if symbol.startswith('O:'):
            return self._get_options_quote(symbol)
        else:
            return self._get_stock_quote(symbol)

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

    def place_order(self, symbol: str, quantity: int, side: str, order_type: str = "MKT",
                   price: Optional[float] = None, enforce: str = "GTC",
                   outside_regular_trading_hour: bool = False,
                   stp_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not self.authenticated:
            if not self._login_attempted:
                self.login()
            if not self.authenticated:
                logger.error("Unable to place order: not authenticated.")
                return None

        try:
            # Check if it's an options symbol
            if symbol.startswith('O:'):
                return self._place_options_order(symbol, quantity, side, order_type, price, enforce)
            else:
                return self._place_stock_order(symbol, quantity, side, order_type, price, enforce)

        except Exception as e:
            logger.error(f"❌ Failed to place order: {e}")
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
        logger.info(f"✅ Stock order placed: {order.id} for {quantity} {symbol} {side}")

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
        """Place options order"""
        try:
            # Remove 'O:' prefix for Alpaca
            option_symbol = symbol[2:] if symbol.startswith('O:') else symbol
            alpaca_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL

            # Create option leg
            option_leg = OptionLegRequest(
                symbol=option_symbol,
                qty=quantity,
                side=alpaca_side
            )

            # For now, create a simple single-leg options order
            # In a real implementation, you'd want more sophisticated order types
            order_request = MarketOrderRequest(
                symbol=option_symbol,
                qty=quantity,
                side=alpaca_side,
                type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                asset_class=AssetClass.OPTION
            )

            order = self.trading_client.submit_order(order_request)
            logger.info(f"✅ Options order placed: {order.id} for {quantity} {symbol} {side}")

            return {
                'order_id': order.id,
                'symbol': symbol,
                'quantity': order.qty,
                'side': order.side.value,
                'type': order.type.value,
                'status': order.status.value,
                'asset_class': 'option'
            }

        except Exception as e:
            logger.error(f"❌ Failed to place options order for {symbol}: {e}")
            return None

    def get_options_chain(self, underlying_symbol: str, expiration_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get options chain for an underlying symbol"""
        if not self.authenticated:
            if not self._login_attempted:
                self.login()
            if not self.authenticated:
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
            logger.error(f"❌ Failed to get options chain for {underlying_symbol}: {e}")
            return None

    def get_current_orders(self):
        if not self.authenticated:
            if not self._login_attempted:
                self.login()
            if not self.authenticated:
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
            logger.error(f"❌ Failed to fetch current orders: {e}")
            return None

    def get_order_history(self, limit: int = 50):
        if not self.authenticated:
            if not self._login_attempted:
                self.login()
            if not self.authenticated:
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
            logger.error(f"❌ Failed to fetch order history: {e}")
            return None

# Create singleton instance
alpaca_client = AlpacaClient()

# Legacy compatibility - webull_client now uses Alpaca internally
webull_client = alpaca_client
