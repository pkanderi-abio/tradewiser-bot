"""
Audit persistence — SQLite-backed trade audit log + AI decision log.

Two tables in a single DB file (path from settings.AI_AUDIT_DB_PATH):

  trade_audit    — every BUY/SELL/dry-run attempt with status + result JSON
  ai_decisions   — every LLM call with prompt hash, model, latency, response,
                   circuit-breaker outcome, and the final decision payload

The trade_audit table preserves the prior record_audit_entry / get_audit_log /
get_audit_entry public API so existing callers (trading_engine, routes/trades)
work unchanged. The DB file is created lazily on first use.

Use a thread-local connection + RLock — SQLite is concurrent-readers-safe but
single-writer; the lock prevents interleaved writes from the FastAPI threadpool
and the asyncio.to_thread trading loop.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_initialized_path: Optional[str] = None


def _connect() -> sqlite3.Connection:
    """Return the process-wide SQLite connection, opening it on first use.

    Re-opens if AI_AUDIT_DB_PATH changes (tests can monkeypatch the setting).
    """
    global _conn, _initialized_path
    target = settings.AI_AUDIT_DB_PATH or "tradewiser_audit.db"
    with _lock:
        if _conn is None or _initialized_path != target:
            if _conn is not None:
                try:
                    _conn.close()
                except Exception:
                    pass
            if target != ":memory:":
                Path(target).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(
                target,
                check_same_thread=False,
                isolation_level=None,  # autocommit; we guard with _lock
            )
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _init_schema(_conn)
            _initialized_path = target
        return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_audit (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            payload    TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT NOT NULL,
            symbol            TEXT NOT NULL,
            proposed_action   TEXT NOT NULL,
            final_action      TEXT NOT NULL,
            confidence        REAL NOT NULL,
            provider          TEXT NOT NULL,
            model             TEXT NOT NULL,
            prompt_hash       TEXT NOT NULL,
            latency_ms        INTEGER,
            attempts          INTEGER NOT NULL,
            circuit_state     TEXT NOT NULL,
            outcome           TEXT NOT NULL,
            error             TEXT,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            stage             TEXT,
            payload           TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS headline_scores (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            headline_hash  TEXT NOT NULL,
            symbol         TEXT NOT NULL,
            event_type     TEXT,
            severity       INTEGER,
            confidence     REAL,
            reason         TEXT,
            model          TEXT,
            timestamp      TEXT NOT NULL,
            UNIQUE(headline_hash)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_headline_scores_symbol ON headline_scores(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_headline_scores_ts ON headline_scores(timestamp)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            symbol          TEXT NOT NULL,
            proposed_action TEXT NOT NULL,
            approved        INTEGER NOT NULL,
            breaches        TEXT NOT NULL,
            reason          TEXT NOT NULL,
            snapshot        TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            equity      REAL NOT NULL,
            cash        REAL,
            buying_power REAL,
            payload     TEXT NOT NULL
        )
        """
    )
    # news_events: audit log for the Phase 2 news_event_extractor. Append-only,
    # one row per extraction attempt including failures. Distinct from
    # headline_scores (which is a lightweight cache table upserted by hash).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT NOT NULL,
            symbol            TEXT NOT NULL,
            headline_hash     TEXT NOT NULL,
            headline          TEXT NOT NULL,
            source            TEXT,
            published_at      TEXT,
            event_type        TEXT NOT NULL,
            severity          INTEGER NOT NULL,
            confidence        REAL NOT NULL,
            reason            TEXT,
            provider          TEXT NOT NULL,
            model             TEXT NOT NULL,
            prompt_hash       TEXT,
            latency_ms        INTEGER,
            attempts          INTEGER,
            circuit_state     TEXT,
            outcome           TEXT NOT NULL,
            error             TEXT,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER
        )
        """
    )
    # multi_day_positions: Phase 3 state store for the NewsEventStrategy.
    # State machine: 'pending' (BUY order submitted, awaiting fill) ->
    #   'open' (filled, tracking exits) -> 'closed' (exited).
    # Exit reasons: 'stop' | 'target' | 'time' | 'reversal' | 'manual' | 'error'.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS multi_day_positions (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy          TEXT NOT NULL,
            symbol            TEXT NOT NULL,
            underlying        TEXT NOT NULL,
            instrument        TEXT NOT NULL,
            state             TEXT NOT NULL,
            entry_signal_at   TEXT NOT NULL,
            entry_order_id    TEXT,
            entry_filled_at   TEXT,
            entry_price       REAL,
            shares            INTEGER,
            stop_level        REAL,
            target_level      REAL,
            hold_until        TEXT,
            entry_severity    REAL,
            entry_event_type  TEXT,
            entry_reason      TEXT,
            exit_order_id     TEXT,
            exit_filled_at    TEXT,
            exit_price        REAL,
            exit_reason       TEXT,
            realized_pnl      REAL,
            last_updated_at   TEXT NOT NULL,
            payload           TEXT
        )
        """
    )
    # Idempotent migrations for old DBs created before token columns existed.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_decisions)").fetchall()}
    if "prompt_tokens" not in cols:
        conn.execute("ALTER TABLE ai_decisions ADD COLUMN prompt_tokens INTEGER")
    if "completion_tokens" not in cols:
        conn.execute("ALTER TABLE ai_decisions ADD COLUMN completion_tokens INTEGER")
    if "stage" not in cols:
        conn.execute("ALTER TABLE ai_decisions ADD COLUMN stage TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_audit_ts ON trade_audit(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_decisions_ts ON ai_decisions(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_decisions_symbol ON ai_decisions(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_risk_events_ts ON risk_events(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_snapshots_ts ON account_snapshots(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_events_ts ON news_events(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_events_symbol ON news_events(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_events_hash ON news_events(headline_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mdp_state ON multi_day_positions(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mdp_underlying ON multi_day_positions(underlying)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mdp_strategy ON multi_day_positions(strategy)")


# ── trade_audit (legacy public API) ────────────────────────────────────────────

def record_audit_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Save a trade audit entry and return it with id + timestamp populated."""
    timestamp = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "INSERT INTO trade_audit (timestamp, payload) VALUES (?, ?)",
            (timestamp, json.dumps(entry, default=str)),
        )
        new_id = cur.lastrowid
    return {"id": new_id, **entry, "timestamp": timestamp}


def get_audit_log(limit: int = 100) -> List[Dict[str, Any]]:
    """Return the most recent trade audit entries (oldest first within the slice)."""
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, timestamp, payload FROM trade_audit ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    out = []
    for row in reversed(rows):
        payload = json.loads(row["payload"])
        out.append({"id": row["id"], **payload, "timestamp": row["timestamp"]})
    return out


def get_audit_entry(entry_id: int) -> Optional[Dict[str, Any]]:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT id, timestamp, payload FROM trade_audit WHERE id = ?",
            (int(entry_id),),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload"])
    return {"id": row["id"], **payload, "timestamp": row["timestamp"]}


# ── ai_decisions (new) ─────────────────────────────────────────────────────────

def record_ai_decision(entry: Dict[str, Any]) -> int:
    """Persist one LLM decision attempt.

    Required keys: symbol, proposed_action, final_action, confidence,
    provider, model, prompt_hash, attempts, circuit_state, outcome, payload (dict).
    Optional: latency_ms, error, prompt_tokens, completion_tokens, stage.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            """
            INSERT INTO ai_decisions
                (timestamp, symbol, proposed_action, final_action, confidence,
                 provider, model, prompt_hash, latency_ms, attempts,
                 circuit_state, outcome, error, prompt_tokens, completion_tokens, stage, payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                timestamp,
                entry["symbol"],
                entry["proposed_action"],
                entry["final_action"],
                float(entry["confidence"]),
                entry["provider"],
                entry["model"],
                entry["prompt_hash"],
                entry.get("latency_ms"),
                int(entry["attempts"]),
                entry["circuit_state"],
                entry["outcome"],
                entry.get("error"),
                entry.get("prompt_tokens"),
                entry.get("completion_tokens"),
                entry.get("stage", "stage1"),
                json.dumps(entry["payload"], default=str),
            ),
        )
        return cur.lastrowid


def record_headline_score(symbol: str, score: Dict[str, Any]) -> None:
    """Persist a scored headline (idempotent via hash).

    score must have: headline_hash, event_type, severity, confidence, reason, model (opt)
    """
    ts = datetime.now(timezone.utc).isoformat()
    h = score["headline_hash"]
    with _lock:
        conn = _connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO headline_scores
            (headline_hash, symbol, event_type, severity, confidence, reason, model, timestamp)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                h,
                symbol.upper(),
                score.get("event_type"),
                int(score.get("severity", 0)),
                float(score.get("confidence", 0.5)),
                score.get("reason"),
                score.get("model"),
                ts,
            ),
        )


def get_headline_scores(symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        conn = _connect()
        if symbol:
            rows = conn.execute(
                "SELECT * FROM headline_scores WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM headline_scores ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def record_risk_event(entry: Dict[str, Any]) -> int:
    """Persist one pre-trade risk gate decision.

    Required keys: symbol, proposed_action, approved (bool), breaches (list),
                   reason (str), snapshot (dict).
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            """
            INSERT INTO risk_events
                (timestamp, symbol, proposed_action, approved, breaches, reason, snapshot)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                timestamp,
                entry["symbol"],
                entry["proposed_action"],
                1 if entry["approved"] else 0,
                json.dumps(entry["breaches"], default=str),
                entry["reason"],
                json.dumps(entry["snapshot"], default=str),
            ),
        )
        return cur.lastrowid


def get_risk_events(limit: int = 100, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    with _lock:
        conn = _connect()
        if symbol:
            rows = conn.execute(
                "SELECT * FROM risk_events WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol.upper(), max(1, int(limit))),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM risk_events ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
    return [
        {
            **dict(r),
            "approved": bool(r["approved"]),
            "breaches": json.loads(r["breaches"]),
            "snapshot": json.loads(r["snapshot"]),
        }
        for r in rows
    ]


def record_account_snapshot(equity: float, cash: Optional[float] = None,
                            buying_power: Optional[float] = None,
                            payload: Optional[Dict[str, Any]] = None) -> int:
    """Persist a point-in-time account equity reading.

    The risk gate calls this whenever it sees a fresh quote so the drawdown
    check has a rolling peak to compare against.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            """
            INSERT INTO account_snapshots (timestamp, equity, cash, buying_power, payload)
            VALUES (?,?,?,?,?)
            """,
            (timestamp, float(equity), cash, buying_power, json.dumps(payload or {}, default=str)),
        )
        return cur.lastrowid


def get_peak_equity(window_days: int) -> Optional[float]:
    """Return the rolling-window peak equity, or None if no snapshots exist."""
    with _lock:
        conn = _connect()
        row = conn.execute(
            """
            SELECT MAX(equity) AS peak
            FROM account_snapshots
            WHERE timestamp >= datetime('now', ?)
            """,
            (f"-{int(window_days)} days",),
        ).fetchone()
    if row is None or row["peak"] is None:
        return None
    return float(row["peak"])


def ai_token_stats(window_minutes: int = 1440) -> Dict[str, Any]:
    """Token spend rollup over a time window — for /trades/ai-status cost telemetry."""
    with _lock:
        conn = _connect()
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS calls,
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
            FROM ai_decisions
            WHERE timestamp >= datetime('now', ?)
              AND outcome = 'ok'
            """,
            (f"-{int(window_minutes)} minutes",),
        ).fetchone()
        by_symbol = conn.execute(
            """
            SELECT symbol,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens
            FROM ai_decisions
            WHERE timestamp >= datetime('now', ?)
              AND outcome = 'ok'
            GROUP BY symbol
            ORDER BY (prompt_tokens + completion_tokens) DESC
            LIMIT 20
            """,
            (f"-{int(window_minutes)} minutes",),
        ).fetchall()
    return {
        "window_minutes": window_minutes,
        "total_calls": row["calls"] if row else 0,
        "total_prompt_tokens": int(row["prompt_tokens"]) if row else 0,
        "total_completion_tokens": int(row["completion_tokens"]) if row else 0,
        "avg_latency_ms": round(float(row["avg_latency_ms"] or 0), 1) if row else 0.0,
        "top_symbols": [dict(r) for r in by_symbol],
    }


def get_ai_decisions(limit: int = 100, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    with _lock:
        conn = _connect()
        if symbol:
            rows = conn.execute(
                "SELECT * FROM ai_decisions WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol.upper(), max(1, int(limit))),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
    return [
        {**dict(r), "payload": json.loads(r["payload"])}
        for r in rows
    ]


def ai_decision_stats(window_minutes: int = 60) -> Dict[str, Any]:
    """Roll up recent decision outcomes for the /ai-status endpoint."""
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """
            SELECT outcome, COUNT(*) AS n
            FROM ai_decisions
            WHERE timestamp >= datetime('now', ?)
            GROUP BY outcome
            """,
            (f"-{int(window_minutes)} minutes",),
        ).fetchall()
    by_outcome = {r["outcome"]: r["n"] for r in rows}
    total = sum(by_outcome.values())
    return {
        "window_minutes": window_minutes,
        "total": total,
        "by_outcome": by_outcome,
        "error_rate": round(
            (by_outcome.get("llm_error", 0) + by_outcome.get("circuit_open", 0)) / total, 3
        ) if total else 0.0,
    }


def reset_for_tests() -> None:
    """Close + drop the connection so the next call reopens with a fresh path.

    Tests that swap AI_AUDIT_DB_PATH per-test call this to force re-init.
    """
    global _conn, _initialized_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None
        _initialized_path = None


def truncate_tables_for_tests(*tables: str) -> None:
    """Clear specific tables — test-only helper. No-op for unknown table names."""
    allowed = {"trade_audit", "ai_decisions", "risk_events", "account_snapshots",
               "headline_scores", "news_events", "multi_day_positions"}
    with _lock:
        conn = _connect()
        for t in tables:
            if t in allowed:
                conn.execute(f"DELETE FROM {t}")


# ── news_events (Phase 2 news event extractor audit) ─────────────────────────

def record_news_event(entry: Dict[str, Any]) -> int:
    """Persist one extraction attempt (success or failure).

    Required keys: symbol, headline_hash, headline, event_type, severity,
                   confidence, provider, model, outcome.
    Optional: source, published_at, reason, prompt_hash, latency_ms, attempts,
              circuit_state, error, prompt_tokens, completion_tokens.

    Outcomes are aligned with ai_decisions vocabulary so downstream dashboards
    can share query logic: 'ok', 'llm_error', 'timeout', 'schema_error',
    'circuit_open', 'kill_switch', 'cache_hit'.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            """
            INSERT INTO news_events
                (timestamp, symbol, headline_hash, headline, source, published_at,
                 event_type, severity, confidence, reason,
                 provider, model, prompt_hash, latency_ms, attempts,
                 circuit_state, outcome, error, prompt_tokens, completion_tokens)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                timestamp,
                entry["symbol"].upper(),
                entry["headline_hash"],
                entry["headline"][:500],
                entry.get("source"),
                entry.get("published_at"),
                entry["event_type"],
                int(entry["severity"]),
                float(entry["confidence"]),
                (entry.get("reason") or "")[:200],
                entry["provider"],
                entry["model"],
                entry.get("prompt_hash"),
                entry.get("latency_ms"),
                entry.get("attempts"),
                entry.get("circuit_state"),
                entry["outcome"],
                entry.get("error"),
                entry.get("prompt_tokens"),
                entry.get("completion_tokens"),
            ),
        )
        return cur.lastrowid


def get_news_events(limit: int = 100, symbol: Optional[str] = None,
                    outcome: Optional[str] = "ok") -> List[Dict[str, Any]]:
    """Return recent extractions. Default filters to outcome='ok' for signal queries."""
    with _lock:
        conn = _connect()
        clauses = []
        args: List[Any] = []
        if symbol:
            clauses.append("symbol = ?"); args.append(symbol.upper())
        if outcome:
            clauses.append("outcome = ?"); args.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(max(1, int(limit)))
        rows = conn.execute(
            f"SELECT * FROM news_events {where} ORDER BY id DESC LIMIT ?",
            args,
        ).fetchall()
    return [dict(r) for r in rows]


def get_news_event_by_hash(headline_hash: str) -> Optional[Dict[str, Any]]:
    """Most recent successful extraction for a given headline_hash (cache lookahead)."""
    with _lock:
        conn = _connect()
        row = conn.execute(
            """
            SELECT * FROM news_events
            WHERE headline_hash = ? AND outcome = 'ok'
            ORDER BY id DESC LIMIT 1
            """,
            (headline_hash,),
        ).fetchone()
    return dict(row) if row else None


def news_event_stats(window_minutes: int = 1440) -> Dict[str, Any]:
    """Rollup of extractor health + volume — for /trades/news-event-status."""
    with _lock:
        conn = _connect()
        overall = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                   COALESCE(AVG(latency_ms),0) AS avg_latency_ms
            FROM news_events
            WHERE timestamp >= datetime('now', ?)
              AND outcome = 'ok'
            """,
            (f"-{int(window_minutes)} minutes",),
        ).fetchone()
        by_outcome = conn.execute(
            """
            SELECT outcome, COUNT(*) AS n
            FROM news_events
            WHERE timestamp >= datetime('now', ?)
            GROUP BY outcome
            """,
            (f"-{int(window_minutes)} minutes",),
        ).fetchall()
        by_type = conn.execute(
            """
            SELECT event_type, COUNT(*) AS n, COALESCE(AVG(severity),0) AS mean_severity
            FROM news_events
            WHERE timestamp >= datetime('now', ?)
              AND outcome = 'ok'
            GROUP BY event_type
            ORDER BY n DESC
            LIMIT 20
            """,
            (f"-{int(window_minutes)} minutes",),
        ).fetchall()
    outcome_counts = {r["outcome"]: r["n"] for r in by_outcome}
    total_attempts = sum(outcome_counts.values())
    fail_bucket = (outcome_counts.get("llm_error", 0)
                   + outcome_counts.get("timeout", 0)
                   + outcome_counts.get("schema_error", 0)
                   + outcome_counts.get("circuit_open", 0))
    return {
        "window_minutes": window_minutes,
        "total_ok": overall["n"] if overall else 0,
        "total_attempts": total_attempts,
        "by_outcome": outcome_counts,
        "error_rate": round(fail_bucket / total_attempts, 3) if total_attempts else 0.0,
        "total_prompt_tokens": int(overall["prompt_tokens"]) if overall else 0,
        "total_completion_tokens": int(overall["completion_tokens"]) if overall else 0,
        "avg_latency_ms": round(float(overall["avg_latency_ms"] or 0), 1) if overall else 0.0,
        "by_event_type": [dict(r) for r in by_type],
    }
