"""Diagnose the trading bot's realized P&L from the audit DB.

Read-only report over the trade_audit / ai_decisions / risk_events tables.
Answers "why is the bot bleeding" with numbers instead of shape-reading a chart.

Usage on the Pi:
    sudo -u tradewiser python3 /opt/tradewiser/scripts/diagnose_bleed.py --days 90
    # or with an explicit DB path:
    sudo -u tradewiser python3 /opt/tradewiser/scripts/diagnose_bleed.py \\
        --db /opt/tradewiser/tradewiser_audit.db --days 90

Sections:
  1. Daily realized P&L (which days bled, best/worst).
  2. Per-underlying totals (which names concentrated the loss).
  3. Win/loss stats (hit rate, avg winner vs avg loser, ratio).
  4. AI outcome breakdown by week (did the fail-closed guardrails work?).
  5. Risk-gate evaluations (was the gate earning its keep?).

Stdlib only. Options P&L is multiplied by 100 (single-contract convention);
equity P&L is left as-is.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple


OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def is_option(symbol: str) -> bool:
    s = symbol[2:] if symbol.startswith("O:") else symbol
    return bool(OCC_RE.match(s))


def underlying_of(symbol: str) -> str:
    s = symbol[2:] if symbol.startswith("O:") else symbol
    m = OCC_RE.match(s)
    return m.group(1) if m else symbol


def fill_price(payload: dict) -> Optional[float]:
    """Prefer Alpaca's filled_avg_price (from the order result); fall back to
    the requested `price` field. Mirrors app/services/pnl.py exactly."""
    result = payload.get("result") or {}
    fill = result.get("filled_avg_price")
    if fill is not None:
        try:
            return float(fill)
        except (TypeError, ValueError):
            pass
    try:
        p = float(payload.get("price") or 0)
        return p or None
    except (TypeError, ValueError):
        return None


def parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Loaders ──────────────────────────────────────────────────────────────────


def load_trade_audit(conn: sqlite3.Connection, start_iso: str) -> List[dict]:
    rows = conn.execute(
        "SELECT id, timestamp, payload FROM trade_audit "
        "WHERE timestamp >= ? ORDER BY id ASC",
        (start_iso,),
    ).fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        payload["id"] = row["id"]
        payload["timestamp"] = row["timestamp"]
        out.append(payload)
    return out


def load_ai_decisions(conn: sqlite3.Connection, start_iso: str) -> List[dict]:
    try:
        rows = conn.execute(
            "SELECT timestamp, symbol, proposed_action, final_action, confidence, outcome "
            "FROM ai_decisions WHERE timestamp >= ? ORDER BY id ASC",
            (start_iso,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def load_risk_events(conn: sqlite3.Connection, start_iso: str) -> List[dict]:
    try:
        rows = conn.execute(
            "SELECT timestamp, symbol, proposed_action, approved, breaches, reason "
            "FROM risk_events WHERE timestamp >= ? ORDER BY id ASC",
            (start_iso,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ── FIFO realized P&L ────────────────────────────────────────────────────────


def fifo_realized(entries: List[dict]) -> Tuple[List[dict], Dict[str, float]]:
    """Walk entries oldest→newest, FIFO-match BUY → SELL per symbol.

    Returns:
      trades:      list of closed-pair records
      per_symbol:  {symbol: total_realized_dollars}
    """
    open_lots: Dict[str, List[dict]] = defaultdict(list)
    trades: List[dict] = []
    per_symbol: Dict[str, float] = defaultdict(float)

    for entry in entries:
        if entry.get("status") not in ("submitted", "filled"):
            continue
        sym = entry.get("symbol") or ""
        side = entry.get("side") or ""
        qty = int(entry.get("quantity") or 0)
        price = fill_price(entry)
        ts = entry.get("timestamp")
        if not sym or not qty or price is None or not ts:
            continue

        if side == "BUY":
            open_lots[sym].append({"qty": qty, "price": price, "ts": ts})
        elif side in ("SELL", "SHORT"):
            remaining = qty
            lots = open_lots[sym]
            multiplier = 100.0 if is_option(sym) else 1.0
            while remaining > 0 and lots:
                lot = lots[0]
                matched = min(remaining, lot["qty"])
                pnl = (price - lot["price"]) * matched * multiplier
                entry_dt = parse_iso(lot["ts"])
                exit_dt = parse_iso(ts)
                hold_days = (
                    (exit_dt - entry_dt).days
                    if entry_dt and exit_dt else None
                )
                trades.append({
                    "closed_at": ts,
                    "symbol": sym,
                    "qty": matched,
                    "entry_price": lot["price"],
                    "exit_price": price,
                    "pnl": pnl,
                    "hold_days": hold_days,
                    "is_option": is_option(sym),
                })
                per_symbol[sym] += pnl
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] == 0:
                    lots.pop(0)

    return trades, dict(per_symbol)


# ── Report sections ──────────────────────────────────────────────────────────


def section_daily_pnl(trades: List[dict], days: int) -> str:
    by_day: Dict[str, float] = defaultdict(float)
    for t in trades:
        dt = parse_iso(t["closed_at"])
        if dt:
            by_day[dt.strftime("%Y-%m-%d")] += t["pnl"]

    if not by_day:
        return "  (no closed trades in window)\n"

    days_sorted = sorted(by_day.keys())
    total = sum(by_day.values())
    green = sum(1 for v in by_day.values() if v > 0)
    red = sum(1 for v in by_day.values() if v < 0)
    worst = min(by_day.items(), key=lambda kv: kv[1])
    best = max(by_day.items(), key=lambda kv: kv[1])

    lines = [
        f"  window            : {days_sorted[0]} → {days_sorted[-1]}",
        f"  active days       : {len(by_day)} of {days} in window",
        f"  green / red days  : {green} green, {red} red",
        f"  best day          : {best[0]}  ${best[1]:+,.2f}",
        f"  worst day         : {worst[0]}  ${worst[1]:+,.2f}",
        f"  window total P&L  : ${total:+,.2f}",
        "",
        "  last 14 active days:",
    ]
    for day in days_sorted[-14:]:
        v = by_day[day]
        mark = "+" if v >= 0 else "-"
        lines.append(f"    {day}  {mark}${abs(v):>10,.2f}")
    return "\n".join(lines) + "\n"


def section_per_symbol(per_symbol: Dict[str, float]) -> str:
    """Roll option symbols into their underlying so per-name P&L is meaningful."""
    rolled: Dict[str, float] = defaultdict(float)
    for sym, pnl in per_symbol.items():
        rolled[underlying_of(sym) if is_option(sym) else sym] += pnl

    if not rolled:
        return "  (no closed trades in window)\n"

    ordered = sorted(rolled.items(), key=lambda kv: kv[1])
    losers = [(s, p) for s, p in ordered if p < 0]
    winners = [(s, p) for s, p in ordered if p > 0]
    flat = [(s, p) for s, p in ordered if p == 0]

    lines = [
        f"  {len(rolled)} underlyings traded -- "
        f"{len(winners)} net winners, {len(losers)} net losers, {len(flat)} breakeven",
    ]
    if losers:
        lines.append("")
        lines.append("  top 10 losers (per underlying):")
        for s, p in losers[:10]:
            lines.append(f"    {s:<8} ${p:>12,.2f}")
    if winners:
        lines.append("")
        lines.append("  top 10 winners (per underlying):")
        for s, p in list(reversed(winners))[:10]:
            lines.append(f"    {s:<8} ${p:>12,.2f}")
    return "\n".join(lines) + "\n"


def section_win_rate(trades: List[dict]) -> str:
    if not trades:
        return "  (no closed trades in window)\n"

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    scratches = [t for t in trades if t["pnl"] == 0]

    n = len(trades)
    hit_rate = len(wins) / n * 100 if n else 0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    wl_ratio = abs(avg_win / avg_loss) if avg_loss else float("inf")
    total = sum(t["pnl"] for t in trades)

    holds = [t["hold_days"] for t in trades if t["hold_days"] is not None]
    avg_hold = sum(holds) / len(holds) if holds else 0

    # Interpretation guide baked into the output -- the whole point of this
    # section is to answer "is there edge or not", not to force the reader
    # to remember what a Kelly criterion says.
    breakeven_ratio = (1 - hit_rate / 100) / (hit_rate / 100) if hit_rate else float("inf")
    verdict = (
        "EDGE (wins big enough to cover losses at this hit rate)"
        if wl_ratio > breakeven_ratio and n >= 20
        else "NO EDGE (avg winner not big enough to cover the losses at this hit rate)"
        if n >= 20
        else "SAMPLE TOO SMALL to judge edge (<20 closed trades)"
    )

    lines = [
        f"  closed trades       : {n} ({len(wins)} wins, {len(losses)} losses, {len(scratches)} scratches)",
        f"  hit rate            : {hit_rate:.1f}%",
        f"  avg winner          : ${avg_win:+,.2f}",
        f"  avg loser           : ${avg_loss:+,.2f}",
        f"  win/loss ratio      : {wl_ratio:.2f}",
        f"  breakeven ratio     : {breakeven_ratio:.2f}  (avg winner must exceed avg loser by this)",
        f"  avg holding days    : {avg_hold:.1f}",
        f"  realized total      : ${total:+,.2f}",
        f"  verdict             : {verdict}",
    ]
    return "\n".join(lines) + "\n"


def section_ai_outcomes(decisions: List[dict]) -> str:
    if not decisions:
        return "  (no ai_decisions rows in window)\n"

    FAIL_CLOSED = {"kill_switch", "circuit_open", "llm_error", "timeout",
                   "schema_error", "soft_fail"}

    by_outcome: Dict[str, int] = defaultdict(int)
    by_week: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for d in decisions:
        outcome = d.get("outcome") or "unknown"
        by_outcome[outcome] += 1
        dt = parse_iso(d.get("timestamp") or "")
        if not dt:
            continue
        year, week, _ = dt.isocalendar()
        week_key = f"{year}-W{week:02d}"
        bucket = "fail_closed" if outcome in FAIL_CLOSED else "ok"
        by_week[week_key][bucket] += 1
        by_week[week_key]["total"] += 1

    total = sum(by_outcome.values())
    fc_total = sum(v for k, v in by_outcome.items() if k in FAIL_CLOSED)
    fc_pct = fc_total / total * 100 if total else 0

    lines = [
        f"  total decisions     : {total}",
        f"  fail-closed rate    : {fc_pct:.1f}%  ({fc_total} of {total})",
        "",
        "  outcome breakdown:",
    ]
    for outcome in sorted(by_outcome, key=lambda k: -by_outcome[k]):
        n = by_outcome[outcome]
        pct = n / total * 100 if total else 0
        tag = " (fail-closed)" if outcome in FAIL_CLOSED else ""
        lines.append(f"    {outcome:<15} {n:>6}  {pct:>5.1f}%{tag}")

    lines.append("")
    lines.append("  weekly fail-closed rate (should trend down after 6059f27 + 2b9783f):")
    lines.append(f"    {'week':<12} {'total':>7} {'fail-closed':>13} {'rate':>8}")
    for wk in sorted(by_week):
        stats = by_week[wk]
        t = stats["total"]
        fc = stats["fail_closed"]
        r = fc / t * 100 if t else 0
        lines.append(f"    {wk:<12} {t:>7} {fc:>13} {r:>7.1f}%")
    return "\n".join(lines) + "\n"


def section_risk_gate(events: List[dict]) -> str:
    if not events:
        return "  (no risk_events rows in window -- table missing or gate quiet)\n"

    total = len(events)
    approved = sum(1 for e in events if e.get("approved"))
    blocked = total - approved

    breach_counts: Dict[str, int] = defaultdict(int)
    for e in events:
        if e.get("approved"):
            continue
        try:
            breaches = json.loads(e.get("breaches") or "[]")
        except (json.JSONDecodeError, TypeError):
            breaches = []
        for b in breaches:
            rule = (b.get("rule") or b.get("name") or "unknown") if isinstance(b, dict) else str(b)
            breach_counts[rule] += 1

    block_pct = blocked / total * 100 if total else 0
    lines = [
        f"  total evaluations   : {total}",
        f"  approved / blocked  : {approved} / {blocked}  (block rate {block_pct:.1f}%)",
    ]
    if breach_counts:
        lines.append("")
        lines.append("  blocks by breach type:")
        for rule, cnt in sorted(breach_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {rule:<32} {cnt:>6}")
    return "\n".join(lines) + "\n"


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="/opt/tradewiser/tradewiser_audit.db",
                    help="Path to tradewiser_audit.db (default: %(default)s)")
    ap.add_argument("--days", type=int, default=90,
                    help="Window in days (default: %(default)s)")
    args = ap.parse_args()

    start_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    start_iso = start_dt.isoformat()

    try:
        conn = sqlite3.connect(args.db)
    except sqlite3.OperationalError as e:
        print(f"error: cannot open {args.db}: {e}", file=sys.stderr)
        return 2
    conn.row_factory = sqlite3.Row

    print(f"# Bleed diagnostic  db={args.db}  window={args.days}d  since={start_iso[:10]} UTC\n")

    trades_raw = load_trade_audit(conn, start_iso)
    trades, per_symbol = fifo_realized(trades_raw)

    print("== 1. Daily realized P&L ==")
    print(section_daily_pnl(trades, args.days))
    print("== 2. Per-underlying realized P&L ==")
    print(section_per_symbol(per_symbol))
    print("== 3. Win/loss stats ==")
    print(section_win_rate(trades))
    print("== 4. AI advisor outcomes ==")
    print(section_ai_outcomes(load_ai_decisions(conn, start_iso)))
    print("== 5. Risk-gate evaluations ==")
    print(section_risk_gate(load_risk_events(conn, start_iso)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
