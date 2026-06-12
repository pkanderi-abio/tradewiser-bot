"""
Market regime classifier — decides if the broad environment is hospitable to
the RSI mean-reversion strategy.

The strategy assumes "stocks pull back, then bounce." That assumption breaks in:
  - A confirmed downtrend (price < SMA50 < SMA200) — RSI oversold readings
    keep getting more oversold; we'd be catching a falling knife.
  - Panic VIX (> REGIME_VIX_PANIC_LEVEL) — ATM option premiums explode and
    risk/reward inverts even when the RSI signal is technically valid.

Output is a `RegimeDecision` with:
  - regime: a coarse label (calm_uptrend / chop / elevated_vol / downtrend / panic)
  - allow_new_buys: bool — what the trading engine actually reads
  - reason: human-readable explanation

The regime gate fails-OPEN: if market_data is unreachable we let trading
proceed. The cost of a missed trade due to a stale macro fetch is higher than
the cost of a single trade in a degraded regime (the per-trade AI + risk
gates still apply).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from app.core.config import settings
from app.core.logger import logger
from app.services.market_data import MarketSnapshot, market_data_feed


@dataclass
class RegimeDecision:
    regime: str
    allow_new_buys: bool
    reason: str
    vix: Optional[float]
    spy_trend: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


class RegimeGate:
    def classify(self, snap: Optional[MarketSnapshot] = None) -> RegimeDecision:
        """Return a regime decision based on the latest market snapshot."""
        if not settings.REGIME_GATE_ENABLED:
            return RegimeDecision(
                regime="disabled",
                allow_new_buys=True,
                reason="regime gate disabled",
                vix=None,
                spy_trend=None,
            )

        snap = snap or market_data_feed.snapshot()
        vix = snap.vix
        trend = snap.spy_trend

        # Fail-open: if we have no data, allow trading and warn.
        if vix is None and trend is None:
            logger.warning("[regime] market snapshot empty — fail-open allow")
            return RegimeDecision(
                regime="unknown",
                allow_new_buys=True,
                reason="market data unavailable — fail-open",
                vix=None,
                spy_trend=None,
            )

        # 1. Panic VIX
        if vix is not None and vix >= settings.REGIME_VIX_PANIC_LEVEL:
            reason = f"VIX {vix:.1f} >= panic level {settings.REGIME_VIX_PANIC_LEVEL:.1f}"
            return RegimeDecision(
                regime="panic",
                allow_new_buys=not settings.REGIME_BLOCK_ON_PANIC_VIX,
                reason=reason,
                vix=vix,
                spy_trend=trend,
            )

        # 2. Confirmed downtrend
        if trend == "downtrend":
            return RegimeDecision(
                regime="downtrend",
                allow_new_buys=not settings.REGIME_BLOCK_ON_DOWNTREND,
                reason="SPY in confirmed downtrend (price < SMA50 < SMA200)",
                vix=vix,
                spy_trend=trend,
            )

        # 3. Elevated vol — informational, not blocking
        if vix is not None and vix >= settings.REGIME_VIX_ELEVATED_LEVEL:
            return RegimeDecision(
                regime="elevated_vol",
                allow_new_buys=True,
                reason=f"VIX {vix:.1f} elevated but below panic; proceeding with caution",
                vix=vix,
                spy_trend=trend,
            )

        # 4. Calm uptrend or chop
        if trend == "uptrend":
            return RegimeDecision(
                regime="calm_uptrend",
                allow_new_buys=True,
                reason="SPY uptrend, VIX normal — favorable for mean-reversion buys",
                vix=vix,
                spy_trend=trend,
            )

        return RegimeDecision(
            regime="chop",
            allow_new_buys=True,
            reason="no strong trend, VIX normal",
            vix=vix,
            spy_trend=trend,
        )

    def snapshot(self) -> dict:
        """Combined market + regime view for the /trades/market-regime endpoint."""
        market = market_data_feed.snapshot()
        regime = self.classify(market)
        return {
            "regime": regime.to_dict(),
            "market": market.to_dict(),
            "thresholds": {
                "vix_panic": settings.REGIME_VIX_PANIC_LEVEL,
                "vix_elevated": settings.REGIME_VIX_ELEVATED_LEVEL,
                "block_on_downtrend": settings.REGIME_BLOCK_ON_DOWNTREND,
                "block_on_panic_vix": settings.REGIME_BLOCK_ON_PANIC_VIX,
            },
        }


regime_gate = RegimeGate()
