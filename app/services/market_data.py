"""
Market-wide data feeds — VIX, breadth, SPY/QQQ trend.

All data here is free via yfinance. No API keys required. Used by:
  - regime.py        — to classify the macro environment
  - ai_advisor.py    — to enrich the LLM prompt with macro context

Each fetch is cached for MARKET_DATA_CACHE_TTL seconds (default 5 minutes) so
the trading loop and the AI prompts share the same snapshot and don't hammer
Yahoo. Cache is in-memory per-process — that's fine; a fresh fetch costs
~200 ms and the trading loop runs once a minute.

All fetches fail-soft: a yfinance hiccup returns `None` rather than raising,
so callers can degrade gracefully.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from threading import RLock
from typing import Dict, Optional, Tuple

import yfinance as yf

from app.core.logger import logger


_CACHE_TTL = 300  # 5 minutes


@dataclass
class MarketSnapshot:
    """A normalized view of macro conditions for the strategy and the LLM."""
    vix: Optional[float]
    vix_pct_change: Optional[float]     # day-over-day % change
    spy_price: Optional[float]
    spy_sma50: Optional[float]
    spy_sma200: Optional[float]
    spy_trend: Optional[str]            # "uptrend" | "downtrend" | "chop" | None
    spy_distance_to_sma50_pct: Optional[float]
    qqq_price: Optional[float]
    qqq_trend: Optional[str]
    fetched_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class MarketDataFeed:
    def __init__(self) -> None:
        self._lock = RLock()
        self._cache: Optional[Tuple[float, MarketSnapshot]] = None

    def snapshot(self, force: bool = False) -> MarketSnapshot:
        """Return current macro snapshot, refreshing if cache expired."""
        with self._lock:
            if not force and self._cache is not None:
                ts, snap = self._cache
                if time.time() - ts < _CACHE_TTL:
                    return snap

        snap = self._fetch()
        with self._lock:
            self._cache = (time.time(), snap)
        return snap

    # ── internals ────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_trend(price: Optional[float], sma50: Optional[float],
                        sma200: Optional[float]) -> Optional[str]:
        """Simple 3-bucket trend label from 50/200-day SMAs."""
        if price is None or sma50 is None or sma200 is None:
            return None
        if price > sma50 > sma200:
            return "uptrend"
        if price < sma50 < sma200:
            return "downtrend"
        return "chop"

    def _fetch(self) -> MarketSnapshot:
        vix, vix_pct = self._fetch_vix()
        spy_price, spy_sma50, spy_sma200 = self._fetch_index("SPY")
        qqq_price, qqq_sma50, qqq_sma200 = self._fetch_index("QQQ")

        spy_trend = self._classify_trend(spy_price, spy_sma50, spy_sma200)
        qqq_trend = self._classify_trend(qqq_price, qqq_sma50, qqq_sma200)

        spy_dist_50 = None
        if spy_price and spy_sma50:
            spy_dist_50 = round((spy_price - spy_sma50) / spy_sma50 * 100, 2)

        return MarketSnapshot(
            vix=vix,
            vix_pct_change=vix_pct,
            spy_price=spy_price,
            spy_sma50=spy_sma50,
            spy_sma200=spy_sma200,
            spy_trend=spy_trend,
            spy_distance_to_sma50_pct=spy_dist_50,
            qqq_price=qqq_price,
            qqq_trend=qqq_trend,
            fetched_at=time.time(),
        )

    @staticmethod
    def _fetch_vix() -> Tuple[Optional[float], Optional[float]]:
        try:
            hist = yf.Ticker("^VIX").history(period="5d")
            if hist.empty:
                return None, None
            close = hist["Close"]
            cur = float(close.iloc[-1])
            prev = float(close.iloc[-2]) if len(close) >= 2 else cur
            pct = round((cur - prev) / prev * 100, 2) if prev else None
            return round(cur, 2), pct
        except Exception as e:
            logger.debug(f"[market_data] VIX fetch failed: {e}")
            return None, None

    @staticmethod
    def _fetch_index(symbol: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        try:
            # 1 year is enough for 200-SMA
            hist = yf.Ticker(symbol).history(period="1y")
            if hist.empty or len(hist) < 50:
                return None, None, None
            close = hist["Close"]
            cur = float(close.iloc[-1])
            sma50 = float(close.rolling(50).mean().iloc[-1])
            sma200 = (
                float(close.rolling(200).mean().iloc[-1])
                if len(close) >= 200 else None
            )
            return round(cur, 2), round(sma50, 2), round(sma200, 2) if sma200 else None
        except Exception as e:
            logger.debug(f"[market_data] {symbol} fetch failed: {e}")
            return None, None, None


market_data_feed = MarketDataFeed()
