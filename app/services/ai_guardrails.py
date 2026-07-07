"""
AI execution guardrails — schema validation, input sanitization, circuit breaker.

Three responsibilities, intentionally co-located so the call path in
ai_advisor stays linear and reviewable:

  1. AIDecision         — pydantic model the LLM response must conform to.
  2. sanitize_headlines — strips prompt-injection vectors from untrusted news.
  3. CircuitBreaker     — short-circuits LLM calls when the provider is sick.

None of these depend on the LLM client. They are unit-testable in isolation.
"""

from __future__ import annotations

import re
import threading
import time
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator


# ── Decision schema ────────────────────────────────────────────────────────────

class AIDecision(BaseModel):
    """The only shape ai_advisor.decide() will accept from the LLM.

    Anything else — missing fields, invalid action, confidence out of range,
    extra prose — is treated as a malformed response and triggers fail-closed.
    """
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def _coerce_reason(cls, v):
        if v is None:
            return ""
        return str(v)[:200]


# Event taxonomy — kept as a top-level tuple so the extractor's prompt, the
# audit schema, and any downstream analytics all reference the same source of
# truth. Values must exactly match the LLM's expected output strings.
NEWS_EVENT_TYPES = (
    "earnings_beat", "earnings_miss", "guidance_raise", "guidance_cut",
    "upgrade", "downgrade", "ma_rumor", "fda_approval", "fda_rejection",
    "lawsuit", "recall", "partnership", "product_launch", "macro", "other",
)


class NewsEvent(BaseModel):
    """One extracted event from a single headline.

    The extractor's LLM response must be a JSON list; each item is validated
    against this schema. Invalid items are dropped (not fail-open) — the
    caller receives the subset that validated cleanly. If the list itself is
    malformed, the entire batch fails and the fail-closed policy applies.
    """
    event_type: Literal[
        "earnings_beat", "earnings_miss", "guidance_raise", "guidance_cut",
        "upgrade", "downgrade", "ma_rumor", "fda_approval", "fda_rejection",
        "lawsuit", "recall", "partnership", "product_launch", "macro", "other",
    ]
    severity: int = Field(ge=-10, le=10)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=200)

    @field_validator("reason", mode="before")
    @classmethod
    def _coerce_reason(cls, v):
        if v is None:
            return ""
        return str(v)[:200]

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, v):
        # Accept float severities from the LLM (some models emit "8.0")
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return 0


# ── Prompt-injection sanitization ──────────────────────────────────────────────

# Patterns that look like attempts to subvert the system prompt when included
# in news headlines. We strip lines or substrings matching these.
_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\b[^.\n]{0,40}\binstructions?\b"),
    re.compile(r"(?i)\bdisregard\b[^.\n]{0,40}\b(instructions?|system|above|prior|previous)\b"),
    re.compile(r"(?i)\byou are now\b"),
    re.compile(r"(?i)\bnew instructions?\s*:"),
    re.compile(r"(?i)<\s*/?\s*(system|instruction|user|assistant|prompt)\s*>"),
    re.compile(r"\[/?(INST|SYS|SYSTEM|USER|ASSISTANT)\]"),
]

# Control chars and exotic unicode that has no business in a news headline.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_headline(headline: str, max_chars: int) -> Optional[str]:
    """Return a safe headline string, or None to drop it entirely.

    - Strips control characters and newlines (collapse to spaces).
    - Drops the headline if it contains a prompt-injection pattern — safer
      than partial redaction because the model still sees the intent.
    - Truncates to max_chars after cleaning.
    """
    if not headline:
        return None
    s = _CONTROL_CHARS.sub("", str(headline))
    s = s.replace("\n", " ").replace("\r", " ").strip()
    if not s:
        return None
    for pat in _INJECTION_PATTERNS:
        if pat.search(s):
            return None
    return s[:max_chars]


def sanitize_headlines(headlines: List[str], max_count: int, max_chars: int) -> List[str]:
    out: List[str] = []
    for h in headlines or []:
        cleaned = sanitize_headline(h, max_chars)
        if cleaned:
            out.append(cleaned)
        if len(out) >= max_count:
            break
    return out


# ── Circuit breaker ────────────────────────────────────────────────────────────

class CircuitState:
    CLOSED = "closed"      # normal operation
    OPEN = "open"          # all calls short-circuit
    HALF_OPEN = "half_open"  # one probe allowed


class CircuitBreaker:
    """Trips after N consecutive failures, cools down, then probes once.

    Thread-safe (RLock). One instance per LLM provider; the advisor owns it.
    """

    def __init__(self, failure_threshold: int, cooldown_seconds: int):
        self._lock = threading.RLock()
        self._threshold = max(1, failure_threshold)
        self._cooldown = max(1, cooldown_seconds)
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None
        self._total_failures = 0
        self._total_successes = 0

    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return CircuitState.CLOSED
            if time.time() - self._opened_at >= self._cooldown:
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN

    def allow(self) -> bool:
        """Return True if a call is permitted right now."""
        return self.state() != CircuitState.OPEN

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._total_successes += 1

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._total_failures += 1
            # Always (re)trip on reaching threshold or on failed probe in half-open.
            # This ensures that a failed half-open probe restarts the cooldown period
            # instead of leaving the breaker "stuck" in half_open forever.
            if self._consecutive_failures >= self._threshold:
                self._opened_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self.state(),
                "consecutive_failures": min(self._consecutive_failures, self._threshold * 2),  # cap for display
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "opened_at": self._opened_at,
                "cooldown_seconds": self._cooldown,
                "failure_threshold": self._threshold,
            }


__all__ = [
    "AIDecision",
    "NewsEvent",
    "NEWS_EVENT_TYPES",
    "ValidationError",
    "sanitize_headline",
    "sanitize_headlines",
    "CircuitBreaker",
    "CircuitState",
]
