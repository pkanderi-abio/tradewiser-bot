"""
P&L computation from the trade_audit log.

Two flavors:
  - realized_pnl_today()        — FIFO-matched realized P&L for the UTC trading day
  - realized_pnl_since(window)  — same, over an arbitrary window

The trade_audit table stores every submitted order with side + quantity + fill
price (when Alpaca returns `filled_avg_price`). Pairing buys to subsequent
sells FIFO-style gives realized P&L without needing a separate ledger.

This is intentionally simple: it does not account for options multipliers (100×)
because the bot trades single contracts and per-contract dollars line up with
the recorded fill prices for the audit-log entries we care about. If you start
trading multi-contract positions or shares, multiply by the appropriate factor
at the call site (or extend this).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services.utils import get_audit_log


def _entry_fill_price(entry: dict) -> Optional[float]:
    """Prefer Alpaca's filled_avg_price over the requested price."""
    result = entry.get("result") or {}
    fill = result.get("filled_avg_price")
    if fill is not None:
        try:
            return float(fill)
        except (TypeError, ValueError):
            pass
    try:
        return float(entry.get("price") or 0) or None
    except (TypeError, ValueError):
        return None


def realized_pnl_since(start_iso: str, limit_entries: int = 2000) -> float:
    """Sum of FIFO-matched (BUY, SELL) realized P&L for orders ts >= start_iso.

    `start_iso` is compared lexicographically against the entry timestamps —
    safe because all timestamps are ISO-8601 UTC.
    """
    open_lots: Dict[str, List[dict]] = defaultdict(list)
    realized = 0.0

    for entry in get_audit_log(limit=limit_entries):
        ts = entry.get("timestamp") or ""
        if ts < start_iso:
            continue
        if entry.get("status") not in ("submitted", "filled"):
            continue
        sym = entry.get("symbol", "")
        side = entry.get("side", "")
        qty = entry.get("quantity") or 0
        price = _entry_fill_price(entry)
        if not sym or not qty or price is None:
            continue

        if side == "BUY":
            open_lots[sym].append({"qty": int(qty), "price": price})
        elif side in ("SELL", "SHORT"):
            remaining = int(qty)
            lots = open_lots.get(sym, [])
            while remaining > 0 and lots:
                lot = lots[0]
                matched = min(remaining, lot["qty"])
                realized += (price - lot["price"]) * matched
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] == 0:
                    lots.pop(0)

    return round(realized, 2)


def _utc_day_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def realized_pnl_today() -> float:
    """FIFO realized P&L for the current UTC day. Used by the risk gate."""
    return realized_pnl_since(_utc_day_start_iso())
