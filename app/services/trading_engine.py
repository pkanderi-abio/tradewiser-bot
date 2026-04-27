import asyncio
from collections import defaultdict, deque
from typing import Dict, List
from app.services.webull_client import webull_client
from app.core.logger import logger
from app.core.config import settings

WATCHLIST = [
    "SPY", "QQQ", "AAPL",  # Stocks
    "O:SPY260427C00700000", "O:SPY260427P00690000",  # SPY Call and Put near current price
    "O:AAPL260427C00290000", "O:AAPL260427P00280000",  # AAPL Call and Put near current price
    "O:QQQ260427C00670000", "O:QQQ260427P00660000"   # QQQ Call and Put near current price
]

# Momentum strategy parameters - ADJUST THESE FOR MORE FREQUENT TRADING
MOMENTUM_WINDOW = 5  # Number of periods to track for momentum
MOMENTUM_THRESHOLD_BUY = 0.002  # 0.2% price increase threshold for buy (reduced for more trading)
MOMENTUM_THRESHOLD_SELL = -0.002  # -0.2% price decrease threshold for sell (reduced for more trading)
TRADE_QUANTITY = 1  # Number of shares/contracts to trade

class MomentumStrategy:
    def __init__(self):
        # Track price history for each symbol
        self.price_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=MOMENTUM_WINDOW))
        # Track current positions
        self.positions: Dict[str, int] = defaultdict(int)
        # Track last trade prices to avoid overtrading
        self.last_trade_price: Dict[str, float] = {}

    def update_price_history(self, symbol: str, price: float):
        """Update price history for momentum calculation"""
        self.price_history[symbol].append(price)

    def calculate_momentum(self, symbol: str) -> float:
        """Calculate momentum as rate of change over the window"""
        prices = list(self.price_history[symbol])
        if len(prices) < 2:
            return 0.0

        # Calculate rate of change: (current - oldest) / oldest
        oldest_price = prices[0]
        current_price = prices[-1]

        if oldest_price == 0:
            return 0.0

        return (current_price - oldest_price) / oldest_price

    def should_buy(self, symbol: str, current_price: float) -> bool:
        """Determine if we should buy based on momentum"""
        if len(self.price_history[symbol]) < MOMENTUM_WINDOW:
            return False  # Need enough data points

        momentum = self.calculate_momentum(symbol)

        # Buy if momentum is positive and above threshold
        if momentum > MOMENTUM_THRESHOLD_BUY:
            # Avoid buying if we already have a position or traded recently at similar price
            last_trade = self.last_trade_price.get(symbol, 0)
            if abs(current_price - last_trade) / max(current_price, last_trade) > 0.01:  # 1% price change since last trade
                return True

        return False

    def should_sell(self, symbol: str, current_price: float) -> bool:
        """Determine if we should sell based on momentum"""
        if self.positions[symbol] <= 0:
            return False  # No position to sell

        momentum = self.calculate_momentum(symbol)

        # Sell if momentum is negative and below threshold
        if momentum < MOMENTUM_THRESHOLD_SELL:
            return True

        return False

    def execute_buy(self, symbol: str, price: float):
        """Execute buy order"""
        try:
            result = webull_client.place_order(
                symbol=symbol,
                quantity=TRADE_QUANTITY,
                side="BUY",
                order_type="MKT"
            )

            if result:
                self.positions[symbol] += TRADE_QUANTITY
                self.last_trade_price[symbol] = price
                logger.info(f"📈 MOMENTUM BUY: {TRADE_QUANTITY} {symbol} @ {price:.2f} (momentum: {self.calculate_momentum(symbol):.4f})")
                return True
            else:
                logger.error(f"❌ Failed to execute buy order for {symbol}")
                return False

        except Exception as e:
            logger.error(f"❌ Buy order error for {symbol}: {e}")
            return False

    def execute_sell(self, symbol: str, price: float):
        """Execute sell order"""
        try:
            quantity_to_sell = min(self.positions[symbol], TRADE_QUANTITY)

            if quantity_to_sell <= 0:
                return False

            result = webull_client.place_order(
                symbol=symbol,
                quantity=quantity_to_sell,
                side="SELL",
                order_type="MKT"
            )

            if result:
                self.positions[symbol] -= quantity_to_sell
                self.last_trade_price[symbol] = price
                logger.info(f"📉 MOMENTUM SELL: {quantity_to_sell} {symbol} @ {price:.2f} (momentum: {self.calculate_momentum(symbol):.4f})")
                return True
            else:
                logger.error(f"❌ Failed to execute sell order for {symbol}")
                return False

        except Exception as e:
            logger.error(f"❌ Sell order error for {symbol}: {e}")
            return False

    def get_status(self) -> Dict:
        """Get current strategy status"""
        return {
            "positions": dict(self.positions),
            "price_history_lengths": {symbol: len(prices) for symbol, prices in self.price_history.items()},
            "last_trade_prices": dict(self.last_trade_price)
        }

# Global strategy instance
momentum_strategy = MomentumStrategy()

async def start_trading_loop():
    logger.info("🚀 Starting momentum trading loop...")
    logger.info(f"📊 Strategy: Momentum | Window: {MOMENTUM_WINDOW} | Buy Threshold: {MOMENTUM_THRESHOLD_BUY:.1%} | Sell Threshold: {MOMENTUM_THRESHOLD_SELL:.1%}")

    # Attempt login once at startup
    login_success = webull_client.login()
    if not login_success:
        logger.warning("Alpaca authentication failed at startup - trades will be simulated in paper trading mode")

    while True:
        try:
            # Fetch quotes and apply momentum strategy
            for symbol in WATCHLIST:
                quote = webull_client.get_quote(symbol)
                if quote:
                    price = float(quote.get("pLast", 0))
                    source = quote.get("source", "unknown")

                    # Update price history
                    momentum_strategy.update_price_history(symbol, price)

                    # Log current price and momentum
                    momentum = momentum_strategy.calculate_momentum(symbol)
                    logger.info(f"{symbol}: ${price:.2f} | Momentum: {momentum:.4f} | Position: {momentum_strategy.positions[symbol]} ({source})")

                    # Apply momentum strategy
                    if momentum_strategy.should_buy(symbol, price):
                        momentum_strategy.execute_buy(symbol, price)

                    elif momentum_strategy.should_sell(symbol, price):
                        momentum_strategy.execute_sell(symbol, price)

            # Log strategy status every 5 cycles
            await asyncio.sleep(settings.POLL_INTERVAL)

        except Exception as e:
            logger.error(f"Trading loop error: {e}")
            await asyncio.sleep(max(settings.POLL_INTERVAL, 10))
