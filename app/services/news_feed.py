"""
News feed — Alpaca news API primary, yfinance fallback.

The legacy ai_advisor used yfinance for headlines. yfinance is slow,
rate-limited, and often returns stale content. Alpaca's news API is real-time
and free with the same API keys we already use for trading.

Falls back to yfinance if Alpaca news is unavailable (older SDK, paper-only
limits, network hiccup, etc.). Both branches sanitize through ai_guardrails so
prompt-injection patterns are dropped before headlines reach the LLM.

Cached per-symbol for settings.AI_NEWS_CACHE_TTL (default 300 s).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Dict, List, Optional, Tuple

import yfinance as yf

from app.core.config import settings
from app.core.logger import logger
from app.services.ai_guardrails import sanitize_headlines


class NewsFeed:
    def __init__(self) -> None:
        self._lock = RLock()
        self._cache: Dict[str, Tuple[float, List[str]]] = {}
        self._alpaca_client = None  # lazy
        self._alpaca_disabled = False

    def headlines(self, symbol: str) -> List[str]:
        """Return sanitized, capped headlines for a symbol. Cached."""
        symbol = symbol.upper()
        with self._lock:
            cached = self._cache.get(symbol)
            if cached:
                ts, items = cached
                if time.time() - ts < settings.AI_NEWS_CACHE_TTL:
                    return items

        raw = self._fetch_alpaca(symbol)
        if not raw:
            raw = self._fetch_yfinance(symbol)

        clean = sanitize_headlines(
            raw,
            max_count=settings.AI_MAX_NEWS_HEADLINES,
            max_chars=settings.AI_MAX_HEADLINE_CHARS,
        )
        with self._lock:
            self._cache[symbol] = (time.time(), clean)
        return clean

    # ── internals ────────────────────────────────────────────────────────────

    def _get_alpaca_client(self):
        if self._alpaca_disabled:
            return None
        if self._alpaca_client is not None:
            return self._alpaca_client
        try:
            from alpaca.data.historical.news import NewsClient
            self._alpaca_client = NewsClient(
                api_key=settings.ALPACA_API_KEY,
                secret_key=settings.ALPACA_SECRET_KEY,
            )
            return self._alpaca_client
        except Exception as e:
            logger.warning(f"[news_feed] Alpaca news client unavailable: {e}")
            self._alpaca_disabled = True
            return None

    def _fetch_alpaca(self, symbol: str) -> List[str]:
        client = self._get_alpaca_client()
        if client is None:
            return []
        try:
            from alpaca.data.requests import NewsRequest
            req = NewsRequest(
                symbols=symbol,
                start=datetime.now(timezone.utc) - timedelta(days=3),
                limit=settings.AI_MAX_NEWS_HEADLINES * 2,
            )
            response = client.get_news(req)
            items = response.data.get("news") if isinstance(response.data, dict) else response.data
            headlines: List[str] = []
            for item in items or []:
                headline = getattr(item, "headline", None) or ""
                if headline:
                    headlines.append(str(headline))
            return headlines
        except Exception as e:
            logger.debug(f"[news_feed] Alpaca news for {symbol} failed: {e}")
            return []

    @staticmethod
    def _fetch_yfinance(symbol: str) -> List[str]:
        try:
            news_items = yf.Ticker(symbol.replace(".", "-")).news or []
            out = []
            for item in news_items[: settings.AI_MAX_NEWS_HEADLINES * 2]:
                content = item.get("content") or {}
                title = item.get("title") or content.get("title") or ""
                if title:
                    out.append(title)
            return out
        except Exception as e:
            logger.debug(f"[news_feed] yfinance news for {symbol} failed: {e}")
            return []


news_feed = NewsFeed()
