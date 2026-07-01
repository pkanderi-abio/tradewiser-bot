"""Multi-day position manager - state machine for the NewsEventStrategy.

Public API:
    position_manager.open_position(...) -> Position     # transitions to 'pending'
    position_manager.record_fill(pos_id, price, ...)    # 'pending' -> 'open'
    position_manager.evaluate_exits(quotes, aggregate_signals) -> List[ExitDecision]
    position_manager.close_position(pos_id, price, reason)  # 'open' -> 'closed'
    position_manager.list_positions(state=None, symbol=None) -> List[Position]
    position_manager.snapshot() -> dict

State machine:
    (none) --open_position--> pending --record_fill--> open --close_position--> closed

Exit reasons (in evaluate priority order):
    stop      : current price <= stop_level
    target    : current price >= target_level
    reversal  : aggregate severity flipped sign with magnitude >= |entry_sev| * REVERSAL_MULT
    time      : today >= hold_until (max hold days elapsed)
    manual    : caller requests
    error     : recoverable-but-abandoned (e.g. persistent broker outage on this position)

Concurrent-slot enforcement is handled by can_open_new_position() which counts
positions in state != 'closed'. The trading engine calls this before submitting
a new BUY order.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logger import logger
from app.services.utils import _connect, _lock


# ── Types ─────────────────────────────────────────────────────────────────────

STATE_PENDING = "pending"
STATE_OPEN = "open"
STATE_CLOSED = "closed"

EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_REVERSAL = "reversal"
EXIT_TIME = "time"
EXIT_MANUAL = "manual"
EXIT_ERROR = "error"

# Instruments the strategy can hold. Kept as strings so callers can extend
# without touching the position manager itself.
INSTRUMENT_STOCK = "stock"
INSTRUMENT_OPTION = "option"


@dataclass
class Position:
    """One managed position row - mirror of multi_day_positions record."""
    id: Optional[int]
    strategy: str
    symbol: str                # traded symbol (may be OCC option or underlying)
    underlying: str            # underlying ticker for risk-gate concentration
    instrument: str            # stock | option
    state: str                 # pending | open | closed
    entry_signal_at: str
    entry_order_id: Optional[str]
    entry_filled_at: Optional[str]
    entry_price: Optional[float]
    shares: Optional[int]
    stop_level: Optional[float]
    target_level: Optional[float]
    hold_until: Optional[str]  # ISO date
    entry_severity: Optional[float]
    entry_event_type: Optional[str]
    entry_reason: Optional[str]
    exit_order_id: Optional[str]
    exit_filled_at: Optional[str]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    realized_pnl: Optional[float]
    last_updated_at: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitDecision:
    """evaluate_exits() output: one per position that should exit now."""
    position_id: int
    symbol: str
    underlying: str
    instrument: str
    exit_reason: str
    exit_price_estimate: float  # for logging / audit - broker still fills
    entry_price: float
    shares: int
    context: Dict[str, Any]     # supporting evidence for the decision


# ── Persistence helpers ──────────────────────────────────────────────────────

def _row_to_position(row) -> Position:
    d = dict(row)
    payload = {}
    if d.get("payload"):
        try:
            payload = json.loads(d["payload"])
        except Exception:
            payload = {"_raw": str(d["payload"])[:200]}
    return Position(
        id=d["id"],
        strategy=d["strategy"],
        symbol=d["symbol"],
        underlying=d["underlying"],
        instrument=d["instrument"],
        state=d["state"],
        entry_signal_at=d["entry_signal_at"],
        entry_order_id=d.get("entry_order_id"),
        entry_filled_at=d.get("entry_filled_at"),
        entry_price=d.get("entry_price"),
        shares=d.get("shares"),
        stop_level=d.get("stop_level"),
        target_level=d.get("target_level"),
        hold_until=d.get("hold_until"),
        entry_severity=d.get("entry_severity"),
        entry_event_type=d.get("entry_event_type"),
        entry_reason=d.get("entry_reason"),
        exit_order_id=d.get("exit_order_id"),
        exit_filled_at=d.get("exit_filled_at"),
        exit_price=d.get("exit_price"),
        exit_reason=d.get("exit_reason"),
        realized_pnl=d.get("realized_pnl"),
        last_updated_at=d["last_updated_at"],
        payload=payload,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Position Manager ─────────────────────────────────────────────────────────

class PositionManager:
    def __init__(self) -> None:
        self._instance_lock = threading.RLock()

    # ── Public API ─────────────────────────────────────────────────────────

    def open_position(
        self,
        *,
        strategy: str,
        symbol: str,
        underlying: str,
        instrument: str,
        entry_severity: float,
        entry_event_type: str,
        entry_reason: str = "",
        entry_order_id: Optional[str] = None,
        hold_days: Optional[int] = None,
        stop_pct: Optional[float] = None,
        target_pct: Optional[float] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Position:
        """Create a new position in state='pending'.

        hold_days/stop_pct/target_pct default to settings.NEWS_STRATEGY_* values.
        stop_level and target_level are computed once record_fill() lands the
        actual fill price.
        """
        hold_days = hold_days or settings.NEWS_STRATEGY_HOLD_DAYS
        stop_pct = stop_pct if stop_pct is not None else settings.NEWS_STRATEGY_STOP_LOSS_PCT
        target_pct = target_pct if target_pct is not None else settings.NEWS_STRATEGY_TAKE_PROFIT_PCT
        hold_until_date = (datetime.now(timezone.utc) + timedelta(days=hold_days)).date().isoformat()
        payload_json = json.dumps(payload or {"stop_pct": stop_pct, "target_pct": target_pct}, default=str)
        now = _now_iso()

        with _lock:
            conn = _connect()
            cur = conn.execute(
                """
                INSERT INTO multi_day_positions
                    (strategy, symbol, underlying, instrument, state, entry_signal_at,
                     entry_order_id, hold_until, entry_severity, entry_event_type,
                     entry_reason, last_updated_at, payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    strategy, symbol.upper(), underlying.upper(), instrument,
                    STATE_PENDING, now, entry_order_id, hold_until_date,
                    float(entry_severity), entry_event_type, entry_reason,
                    now, payload_json,
                ),
            )
            new_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM multi_day_positions WHERE id = ?", (new_id,)
            ).fetchone()
        pos = _row_to_position(row)
        logger.info(
            f"[position_manager] opened {strategy}/{symbol} pending id={new_id} "
            f"sev={entry_severity} event={entry_event_type} hold_until={hold_until_date}"
        )
        return pos

    def record_fill(
        self,
        position_id: int,
        *,
        fill_price: float,
        shares: int,
        broker_order_id: Optional[str] = None,
    ) -> Optional[Position]:
        """Transition pending -> open, set stop/target levels from fill_price."""
        with _lock:
            conn = _connect()
            row = conn.execute(
                "SELECT * FROM multi_day_positions WHERE id = ?", (position_id,)
            ).fetchone()
            if row is None:
                logger.warning(f"[position_manager] record_fill: id {position_id} not found")
                return None
            pos = _row_to_position(row)
            if pos.state != STATE_PENDING:
                logger.warning(
                    f"[position_manager] record_fill: id {position_id} in state {pos.state}, expected pending"
                )
                return pos
            stop_pct = float(pos.payload.get("stop_pct", settings.NEWS_STRATEGY_STOP_LOSS_PCT))
            target_pct = float(pos.payload.get("target_pct", settings.NEWS_STRATEGY_TAKE_PROFIT_PCT))
            stop_level = fill_price * (1.0 - stop_pct)
            target_level = fill_price * (1.0 + target_pct)
            now = _now_iso()
            conn.execute(
                """
                UPDATE multi_day_positions
                SET state = ?, entry_filled_at = ?, entry_price = ?, shares = ?,
                    stop_level = ?, target_level = ?, entry_order_id = COALESCE(?, entry_order_id),
                    last_updated_at = ?
                WHERE id = ?
                """,
                (STATE_OPEN, now, float(fill_price), int(shares),
                 stop_level, target_level, broker_order_id, now, position_id),
            )
            row = conn.execute(
                "SELECT * FROM multi_day_positions WHERE id = ?", (position_id,)
            ).fetchone()
        logger.info(
            f"[position_manager] filled id={position_id} @ {fill_price:.2f} "
            f"shares={shares} stop={stop_level:.2f} target={target_level:.2f}"
        )
        return _row_to_position(row)

    def close_position(
        self,
        position_id: int,
        *,
        exit_price: float,
        exit_reason: str,
        broker_order_id: Optional[str] = None,
    ) -> Optional[Position]:
        """Transition open -> closed with realized P&L."""
        with _lock:
            conn = _connect()
            row = conn.execute(
                "SELECT * FROM multi_day_positions WHERE id = ?", (position_id,)
            ).fetchone()
            if row is None:
                logger.warning(f"[position_manager] close_position: id {position_id} not found")
                return None
            pos = _row_to_position(row)
            if pos.state == STATE_CLOSED:
                logger.info(f"[position_manager] close_position: id {position_id} already closed")
                return pos
            entry_price = pos.entry_price or 0.0
            shares = pos.shares or 0
            realized = (float(exit_price) - entry_price) * shares
            now = _now_iso()
            conn.execute(
                """
                UPDATE multi_day_positions
                SET state = ?, exit_filled_at = ?, exit_price = ?, exit_reason = ?,
                    exit_order_id = COALESCE(?, exit_order_id),
                    realized_pnl = ?, last_updated_at = ?
                WHERE id = ?
                """,
                (STATE_CLOSED, now, float(exit_price), exit_reason,
                 broker_order_id, realized, now, position_id),
            )
            row = conn.execute(
                "SELECT * FROM multi_day_positions WHERE id = ?", (position_id,)
            ).fetchone()
        logger.info(
            f"[position_manager] closed id={position_id} exit={exit_price:.2f} "
            f"reason={exit_reason} pnl={realized:+.2f}"
        )
        return _row_to_position(row)

    def evaluate_exits(
        self,
        quotes: Dict[str, float],
        aggregate_signals: Optional[Dict[str, float]] = None,
        *,
        as_of: Optional[datetime] = None,
    ) -> List[ExitDecision]:
        """Return a list of positions that should exit right now.

        Args:
            quotes: symbol -> current price (traded symbol, e.g. option OCC or underlying)
            aggregate_signals: underlying -> current aggregate severity (for reversal detection)
            as_of: override the notion of "now" (tests). Defaults to datetime.now(UTC).

        Priority order per position: stop > target > reversal > time.
        Positions in state != 'open' are ignored.
        """
        as_of = as_of or datetime.now(timezone.utc)
        as_of_date = as_of.date()
        aggregate_signals = aggregate_signals or {}
        reversal_mult = float(settings.NEWS_STRATEGY_REVERSAL_SEVERITY_MULT)

        decisions: List[ExitDecision] = []
        for pos in self.list_positions(state=STATE_OPEN):
            price = quotes.get(pos.symbol)
            if price is None:
                # No quote - can't evaluate stops/targets. Skip; caller retries next pass.
                continue
            price = float(price)
            entry = pos.entry_price or 0.0
            shares = pos.shares or 0

            # 1. Stop
            if pos.stop_level is not None and price <= pos.stop_level:
                decisions.append(ExitDecision(
                    position_id=pos.id, symbol=pos.symbol, underlying=pos.underlying,
                    instrument=pos.instrument, exit_reason=EXIT_STOP,
                    exit_price_estimate=price, entry_price=entry, shares=shares,
                    context={"stop_level": pos.stop_level, "quote": price},
                ))
                continue
            # 2. Target
            if pos.target_level is not None and price >= pos.target_level:
                decisions.append(ExitDecision(
                    position_id=pos.id, symbol=pos.symbol, underlying=pos.underlying,
                    instrument=pos.instrument, exit_reason=EXIT_TARGET,
                    exit_price_estimate=price, entry_price=entry, shares=shares,
                    context={"target_level": pos.target_level, "quote": price},
                ))
                continue
            # 3. Reversal
            entry_sev = pos.entry_severity or 0.0
            new_sev = aggregate_signals.get(pos.underlying)
            if new_sev is not None and entry_sev != 0:
                # Reversal fires when the new aggregate has the opposite sign AND its
                # magnitude exceeds |entry_sev| * |reversal_mult|. Configured as a
                # negative multiplier (-0.75 default) so callers can see the sign
                # semantics at a glance.
                threshold = abs(entry_sev) * abs(reversal_mult)
                if entry_sev > 0 and new_sev <= -threshold:
                    decisions.append(ExitDecision(
                        position_id=pos.id, symbol=pos.symbol, underlying=pos.underlying,
                        instrument=pos.instrument, exit_reason=EXIT_REVERSAL,
                        exit_price_estimate=price, entry_price=entry, shares=shares,
                        context={"entry_sev": entry_sev, "new_sev": new_sev, "threshold": -threshold},
                    ))
                    continue
                if entry_sev < 0 and new_sev >= threshold:
                    decisions.append(ExitDecision(
                        position_id=pos.id, symbol=pos.symbol, underlying=pos.underlying,
                        instrument=pos.instrument, exit_reason=EXIT_REVERSAL,
                        exit_price_estimate=price, entry_price=entry, shares=shares,
                        context={"entry_sev": entry_sev, "new_sev": new_sev, "threshold": threshold},
                    ))
                    continue
            # 4. Time stop
            if pos.hold_until:
                try:
                    hu = datetime.fromisoformat(pos.hold_until).date()
                except Exception:
                    hu = None
                if hu is not None and as_of_date >= hu:
                    decisions.append(ExitDecision(
                        position_id=pos.id, symbol=pos.symbol, underlying=pos.underlying,
                        instrument=pos.instrument, exit_reason=EXIT_TIME,
                        exit_price_estimate=price, entry_price=entry, shares=shares,
                        context={"hold_until": pos.hold_until, "as_of": as_of_date.isoformat()},
                    ))
                    continue

        return decisions

    def list_positions(
        self,
        state: Optional[str] = None,
        symbol: Optional[str] = None,
        underlying: Optional[str] = None,
        strategy: Optional[str] = None,
        limit: int = 100,
    ) -> List[Position]:
        clauses = []
        args: List[Any] = []
        if state is not None:
            clauses.append("state = ?"); args.append(state)
        if symbol is not None:
            clauses.append("symbol = ?"); args.append(symbol.upper())
        if underlying is not None:
            clauses.append("underlying = ?"); args.append(underlying.upper())
        if strategy is not None:
            clauses.append("strategy = ?"); args.append(strategy)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(max(1, int(limit)))
        with _lock:
            conn = _connect()
            rows = conn.execute(
                f"SELECT * FROM multi_day_positions {where} ORDER BY id DESC LIMIT ?",
                args,
            ).fetchall()
        return [_row_to_position(r) for r in rows]

    def get_position(self, position_id: int) -> Optional[Position]:
        with _lock:
            conn = _connect()
            row = conn.execute(
                "SELECT * FROM multi_day_positions WHERE id = ?", (position_id,)
            ).fetchone()
        return _row_to_position(row) if row else None

    def can_open_new_position(self, *, strategy: Optional[str] = None) -> bool:
        """True if there is room for another NewsEventStrategy position.

        The concurrent-slot count spans pending + open states across the given
        strategy label (or all news strategies if strategy is None). RSI-strategy
        positions live outside this table and are counted separately by the
        risk gate.
        """
        clauses = ["state IN (?, ?)"]
        args: List[Any] = [STATE_PENDING, STATE_OPEN]
        if strategy is not None:
            clauses.append("strategy = ?"); args.append(strategy)
        with _lock:
            conn = _connect()
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM multi_day_positions WHERE {' AND '.join(clauses)}",
                args,
            ).fetchone()
        open_slots = int(row["n"]) if row else 0
        return open_slots < int(settings.NEWS_STRATEGY_MAX_CONCURRENT)

    def snapshot(self) -> dict:
        with _lock:
            conn = _connect()
            state_counts = conn.execute(
                "SELECT state, COUNT(*) AS n FROM multi_day_positions GROUP BY state"
            ).fetchall()
            recent_closed = conn.execute(
                """
                SELECT COALESCE(SUM(realized_pnl), 0) AS total_pnl,
                       COUNT(*) AS n,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses
                FROM multi_day_positions
                WHERE state = 'closed'
                  AND exit_filled_at >= datetime('now', '-30 days')
                """
            ).fetchone()
        by_state = {r["state"]: r["n"] for r in state_counts}
        n_settled = int(recent_closed["n"]) if recent_closed else 0
        wins = int(recent_closed["wins"] or 0) if recent_closed else 0
        losses = int(recent_closed["losses"] or 0) if recent_closed else 0
        hit_rate = round(wins / n_settled, 3) if n_settled else 0.0
        return {
            "enabled": settings.NEWS_STRATEGY_ENABLED,
            "max_concurrent": settings.NEWS_STRATEGY_MAX_CONCURRENT,
            "by_state": by_state,
            "trailing_30d": {
                "settled": n_settled,
                "wins": wins,
                "losses": losses,
                "hit_rate": hit_rate,
                "total_pnl": float(recent_closed["total_pnl"] or 0.0) if recent_closed else 0.0,
            },
        }


# Singleton consumed by NewsEventStrategy and the trading engine.
position_manager = PositionManager()
