"""
AI Trade Advisor — Groq (free tier) with Ollama local fallback.

Priority:
  1. GROQ_API_KEY set  → Groq cloud (llama-3.3-70b-versatile, 14,400 req/day free)
  2. No key            → Ollama local (llama3.2, free forever, needs Ollama running)

For every BUY or SELL signal the RSI strategy fires, this advisor asks:
  "Given the technical setup, IV environment, earnings risk, and news — is this a good trade?"

Only signals where the LLM returns confidence >= MIN_CONFIDENCE actually execute.
Decisions are cached per symbol for CACHE_TTL seconds to avoid hammering the API.
News is cached per symbol for NEWS_TTL seconds (yfinance is slow).
"""

import json
import time
from typing import Dict, List, Optional, Tuple

import yfinance as yf

from app.core.config import settings
from app.core.logger import logger
from app.services.watchlist_manager import EXPERT_PICKS

MIN_CONFIDENCE = 0.65
CACHE_TTL      = 3600  # reuse decision for 1 hour (daily signal changes once per day)
NEWS_TTL       = 300   # seconds to cache news headlines per symbol

GROQ_MODEL   = "llama-3.3-70b-versatile"
OLLAMA_MODEL = "llama3.2"
OLLAMA_URL   = "http://localhost:11434/v1"


class AIAdvisor:
    def __init__(self):
        self._client = None
        self._decision_cache: Dict[str, Tuple[float, dict]] = {}
        self._news_cache: Dict[str, Tuple[float, List[str]]] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        return True  # always on — Groq or Ollama

    def get_provider(self) -> str:
        return "groq" if settings.GROQ_API_KEY else "ollama"

    def get_model(self) -> str:
        return GROQ_MODEL if settings.GROQ_API_KEY else OLLAMA_MODEL

    def decide(
        self,
        symbol: str,
        price: float,
        momentum: float,
        price_history: List[float],
        position: int,
        proposed_action: str,
        context: Optional[dict] = None,
    ) -> dict:
        """
        Ask the LLM whether to proceed with the proposed trade.

        context (optional) — dict with keys from get_daily_signal():
            rsi, sma50_dist_pct, hv_rank, days_to_earnings, near_sma50, vol_above_avg

        Returns:
            {"action": "BUY|SELL|HOLD", "confidence": float, "reason": str}

        Falls back to approving the signal if the LLM call fails,
        so trading is never fully blocked by an AI outage.
        """
        cached = self._decision_cache.get(symbol)
        if cached:
            ts, decision = cached
            if time.time() - ts < CACHE_TTL:
                return decision

        news      = self._get_news(symbol)
        rationale = EXPERT_PICKS.get(symbol, {}).get("rationale", "")
        expert    = EXPERT_PICKS.get(symbol, {}).get("expert", "")

        decision = self._call_llm(
            symbol, price, position, proposed_action,
            news, rationale, expert, context,
        )

        self._decision_cache[symbol] = (time.time(), decision)
        logger.info(
            f"[AI/{self.get_provider()}] {symbol} | proposed={proposed_action} → "
            f"AI={decision['action']} ({decision['confidence']:.0%}) — {decision['reason']}"
        )
        return decision

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            if settings.GROQ_API_KEY:
                self._client = OpenAI(
                    api_key=settings.GROQ_API_KEY,
                    base_url="https://api.groq.com/openai/v1",
                )
                logger.info(f"[AI] Provider: Groq ({GROQ_MODEL})")
            else:
                self._client = OpenAI(
                    api_key="ollama",
                    base_url=OLLAMA_URL,
                )
                logger.info(f"[AI] Provider: Ollama ({OLLAMA_MODEL}) at {OLLAMA_URL}")
        return self._client

    def _get_news(self, symbol: str) -> List[str]:
        cached = self._news_cache.get(symbol)
        if cached:
            ts, headlines = cached
            if time.time() - ts < NEWS_TTL:
                return headlines
        try:
            news_items = yf.Ticker(symbol.replace(".", "-")).news or []
            headlines = []
            for item in news_items[:6]:
                content = item.get("content") or {}
                title = item.get("title") or content.get("title") or ""
                if title:
                    headlines.append(title[:120])
            self._news_cache[symbol] = (time.time(), headlines)
            return headlines
        except Exception:
            return []

    def _call_llm(
        self,
        symbol: str,
        price: float,
        position: int,
        proposed_action: str,
        news: List[str],
        rationale: str,
        expert: str,
        context: Optional[dict],
    ) -> dict:
        news_str = "\n".join(f"- {h}" for h in news) if news else "(no recent news)"

        # Build technical context block from daily signal data
        if context:
            rsi             = context.get("rsi")
            sma50_dist      = context.get("sma50_dist_pct")
            hv_rank         = context.get("hv_rank")
            days_to_earn    = context.get("days_to_earnings")
            near_sma50      = context.get("near_sma50")
            vol_above_avg   = context.get("vol_above_avg")

            tech_block = f"""--- Technical Signal (Daily RSI Strategy) ---
RSI(14)             : {rsi} {"← OVERSOLD" if rsi is not None and rsi < 35 else "← OVERBOUGHT" if rsi is not None and rsi > 70 else ""}
Price vs SMA50      : {f"{sma50_dist:+.1f}%" if sma50_dist is not None else "N/A"} {"(near SMA50)" if near_sma50 else "(extended above SMA50)" if near_sma50 is False else ""}
Volume vs 20d avg   : {"above average (bullish confirmation)" if vol_above_avg else "below average (weaker signal)" if vol_above_avg is False else "N/A"}
HV rank (IV proxy)  : {f"{hv_rank:.0f}%" if hv_rank is not None else "N/A"} {"(options cheap — favorable)" if hv_rank is not None and hv_rank < 30 else "(options expensive — caution)" if hv_rank is not None and hv_rank > 70 else ""}
Days to earnings    : {days_to_earn if days_to_earn is not None else "unknown"} {"← NEAR EARNINGS — caution" if days_to_earn is not None and days_to_earn <= 14 else ""}"""
        else:
            tech_block = "(no technical context provided)"

        prompt = f"""You are a disciplined options trading advisor reviewing a daily RSI signal.
The strategy buys ATM call options when RSI is oversold and sells when RSI is overbought.
Evaluate whether the proposed {proposed_action} should proceed.

--- Asset ---
Symbol              : {symbol}
Current price       : ${price:.2f}
Open positions      : {position} contract(s) currently held
Proposed action     : {proposed_action}

{tech_block}

--- Expert Thesis ---
Expert              : {expert or "N/A"}
Thesis              : {rationale or "N/A"}

--- Recent News ---
{news_str}

--- Decision Guidelines ---
APPROVE BUY  if: RSI genuinely oversold (<35), price near/above SMA50, HV rank <50%, no near-term earnings, news not severely negative
APPROVE SELL if: RSI overbought (>70), news supports exit, or holding a losing position
OVERRIDE to HOLD if: news strongly negative for BUY, earnings risk <7 days, HV rank >70% (options too expensive), or mixed signals

Respond ONLY with valid JSON (no other text, no markdown):
{{"action": "BUY", "confidence": 0.80, "reason": "under 15 words"}}

action must be exactly BUY, SELL, or HOLD."""

        try:
            client   = self._get_client()
            response = client.chat.completions.create(
                model=self.get_model(),
                max_tokens=80,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            text = response.choices[0].message.content.strip()
            # Strip markdown code fences that some models add
            if "```" in text:
                parts = text.split("```")
                text = parts[1] if len(parts) > 1 else parts[0]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            decision = json.loads(text)
            if decision.get("action") not in ("BUY", "SELL", "HOLD"):
                decision["action"] = "HOLD"
            decision["confidence"] = max(0.0, min(1.0, float(decision.get("confidence") or 0)))
            decision["reason"]     = str(decision.get("reason") or "")[:100]
            return decision

        except Exception as e:
            logger.warning(f"[AI] LLM error for {symbol}: {e} — passing through signal")
            return {"action": proposed_action, "confidence": 0.5, "reason": f"LLM error fallback: {str(e)[:40]}"}


# Singleton
ai_advisor = AIAdvisor()
