"""SQLite-backed cache for backtest AI decisions.

The full-stack backtest calls `ai_advisor.decide()` for every historical
signal. That's real network + real token spend on the first pass; on the
second pass we want it deterministic and free. Cache keys are hashed from
the exact inputs the advisor sees — swap any of them and you get a fresh
lookup, not a stale one.

Kept separate from `utils.record_ai_decision` (the live audit log) so
backtest churn never lands in the production audit table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable, Optional


DEFAULT_CACHE_PATH = "runs/ai_decision_cache.sqlite"


@dataclass
class CachedDecision:
    action: str
    confidence: float
    reason: str


def make_cache_key(
    *,
    symbol: str,
    date_str: str,
    proposed_action: str,
    price: float,
    momentum: float,
    price_history: Iterable[float],
    provider: str,
    model: str,
) -> str:
    """Deterministic key for one (symbol, day, input snapshot).

    Includes provider+model so a model swap invalidates the cache — you
    do NOT want a Claude-Sonnet-3-cached BUY to override what Sonnet-4
    would say today.
    """
    price_hist = list(price_history)
    payload = json.dumps(
        {
            "symbol": symbol.upper(),
            "date": date_str,
            "action": proposed_action.upper(),
            # Round floats — tiny bit-level float drift in re-derived history
            # should not miss the cache.
            "price": round(float(price), 4),
            "momentum": round(float(momentum), 6),
            "hist": [round(float(p), 4) for p in price_hist],
            "provider": provider,
            "model": model,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AIDecisionCache:
    """Simple keyed cache. Thread-safe for concurrent backtest workers."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS ai_decision_cache (
            cache_key   TEXT PRIMARY KEY,
            symbol      TEXT NOT NULL,
            date        TEXT NOT NULL,
            action      TEXT NOT NULL,
            confidence  REAL NOT NULL,
            reason      TEXT NOT NULL,
            provider    TEXT,
            model       TEXT,
            created_at  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_ai_decision_cache_symbol_date
            ON ai_decision_cache(symbol, date);
    """

    def __init__(self, path: str = DEFAULT_CACHE_PATH) -> None:
        self._path = path
        self._lock = RLock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.executescript(self._SCHEMA)

    def get(self, key: str) -> Optional[CachedDecision]:
        with self._lock:
            row = self._conn.execute(
                "SELECT action, confidence, reason FROM ai_decision_cache WHERE cache_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return CachedDecision(action=row[0], confidence=float(row[1]), reason=row[2])

    def put(
        self,
        key: str,
        *,
        symbol: str,
        date_str: str,
        decision: CachedDecision,
        provider: str = "",
        model: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ai_decision_cache "
                "(cache_key, symbol, date, action, confidence, reason, provider, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    symbol.upper(),
                    date_str,
                    decision.action,
                    float(decision.confidence),
                    decision.reason,
                    provider,
                    model,
                    time.time(),
                ),
            )

    def stats(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM ai_decision_cache"
            ).fetchone()
        return {
            "count": int(row[0] or 0),
            "oldest": row[1],
            "newest": row[2],
            "path": self._path,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
