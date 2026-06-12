"""
Social sentiment — StockTwits public stream (no API key).

StockTwits exposes a `streams/symbol/<SYM>.json` endpoint that returns recent
messages with an entities.sentiment.basic field of "Bullish" or "Bearish"
when the poster explicitly tags it. We aggregate the last N messages into a
bull/bear ratio + a mentions count.

Caveats:
  - Public endpoint, rate-limited; we cache aggressively and fail-open.
  - Retail sentiment is noisy; trade it as confirmation, not a primary signal.
  - StockTwits can rate-limit or block; on any failure we return None so callers
    can fall through to "no social signal."

Cached per-symbol for settings.AI_NEWS_CACHE_TTL (we reuse that knob since
sentiment freshness has the same shape as news freshness).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from threading import RLock
from typing import Dict, Optional, Tuple

import requests

from app.core.config import settings
from app.core.logger import logger


@dataclass
class SocialSentiment:
    symbol: str
    bullish_count: int
    bearish_count: int
    tagged_total: int
    mentions: int                # total messages (tagged + untagged)
    bull_ratio: float            # bullish_count / max(1, tagged_total)
    fetched_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class SentimentFeed:
    _BASE = "https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"

    def __init__(self) -> None:
        self._lock = RLock()
        self._cache: Dict[str, Tuple[float, Optional[SocialSentiment]]] = {}
        self._disabled = False  # latch-disable after persistent 429s

    def sentiment(self, symbol: str) -> Optional[SocialSentiment]:
        """Best-effort StockTwits sentiment. None if unavailable."""
        if self._disabled:
            return None
        symbol = symbol.upper()
        with self._lock:
            cached = self._cache.get(symbol)
            if cached:
                ts, val = cached
                if time.time() - ts < settings.AI_NEWS_CACHE_TTL:
                    return val

        result = self._fetch(symbol)
        with self._lock:
            self._cache[symbol] = (time.time(), result)
        return result

    def _fetch(self, symbol: str) -> Optional[SocialSentiment]:
        try:
            r = requests.get(
                self._BASE.format(sym=symbol),
                timeout=4,
                headers={"User-Agent": "tradewiser-bot/1.0"},
            )
            if r.status_code == 429:
                logger.warning(f"[sentiment_feed] StockTwits rate-limited; disabling for this process")
                self._disabled = True
                return None
            if r.status_code != 200:
                return None
            data = r.json()
            messages = data.get("messages") or []
            bull = bear = tagged = 0
            for m in messages:
                ent = (m.get("entities") or {}).get("sentiment") or {}
                basic = ent.get("basic")
                if basic == "Bullish":
                    bull += 1
                    tagged += 1
                elif basic == "Bearish":
                    bear += 1
                    tagged += 1
            return SocialSentiment(
                symbol=symbol,
                bullish_count=bull,
                bearish_count=bear,
                tagged_total=tagged,
                mentions=len(messages),
                bull_ratio=round(bull / max(1, tagged), 3),
                fetched_at=time.time(),
            )
        except Exception as e:
            logger.debug(f"[sentiment_feed] {symbol} fetch failed: {e}")
            return None


sentiment_feed = SentimentFeed()
