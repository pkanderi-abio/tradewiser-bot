"""DailyRSIStrategy stock-equivalent backtest.

Reads raw Alpaca CLI bars from ./raw/, computes signals on completed bar T close,
fills on bar T+1 open with 5 bps slippage. Writes all artifacts to this folder.
Run with: python run.py
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

RUN_DIR = Path(__file__).parent
RAW_DIR = RUN_DIR / "raw"

CONFIG = json.loads((RUN_DIR / "config.json").read_text())
SYMBOLS: List[str] = CONFIG["symbols"]
START = pd.Timestamp(CONFIG["start"]).tz_localize("UTC")
END = pd.Timestamp(CONFIG["end"]).tz_localize("UTC")
INITIAL_CASH: float = float(CONFIG["initial_cash"])
MAX_POSITIONS: int = int(CONFIG["max_concurrent_positions"])
SLIPPAGE_BPS: float = float(CONFIG["slippage_bps"])
RSI_PERIOD: int = int(CONFIG["rsi_period"])
RSI_BUY: float = float(CONFIG["rsi_buy_threshold"])
RSI_SELL: float = float(CONFIG["rsi_sell_threshold"])
SMA_PERIOD: int = int(CONFIG["sma_period"])
VOL_PERIOD: int = int(CONFIG["avg_volume_period"])
NEAR_SMA_PCT: float = float(CONFIG["near_sma_pct"])
REQUIRE_UPTREND: bool = bool(CONFIG.get("require_uptrend_filter", True))

SLIPPAGE = SLIPPAGE_BPS / 10_000.0


# ── Indicator implementations (per skill reference.md) ─────────────────────────

def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI with Wilder's smoothing. Seed = SMA over first `period` deltas."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = pd.Series(np.nan, index=close.index, dtype="float64")
    avg_loss = pd.Series(np.nan, index=close.index, dtype="float64")

    if len(close) < period + 1:
        return pd.Series(np.nan, index=close.index, dtype="float64")

    seed_idx = period  # index where the seed average is placed
    avg_gain.iloc[seed_idx] = gain.iloc[1 : period + 1].mean()
    avg_loss.iloc[seed_idx] = loss.iloc[1 : period + 1].mean()

    for i in range(seed_idx + 1, len(close)):
        prev_g = avg_gain.iloc[i - 1]
        prev_l = avg_loss.iloc[i - 1]
        avg_gain.iloc[i] = (prev_g * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (prev_l * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    return rsi


def simple_sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


# ── Load + normalize bars ──────────────────────────────────────────────────────

def load_bars(symbol: str) -> pd.DataFrame:
    raw_path = RAW_DIR / f"bars_{symbol}.json"
    payload = json.loads(raw_path.read_text())
    bars = payload["bars"]
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low",
                             "c": "close", "v": "volume", "vw": "vwap"})
    df = df.sort_values("ts").reset_index(drop=True)
    df["symbol"] = symbol
    return df[["ts", "symbol", "open", "high", "low", "close", "volume", "vwap"]]


def fingerprint(symbol: str, df: pd.DataFrame, df_filtered: pd.DataFrame) -> dict:
    raw_path = RAW_DIR / f"bars_{symbol}.json"
    raw_bytes = raw_path.read_bytes()
    return {
        "provider": "alpaca",
        "access_method": "alpaca_cli",
        "feed": CONFIG["feed"],
        "adjustment": CONFIG["adjustment"],
        "timeframe": CONFIG["timeframe"],
        "total_bars_fetched": int(len(df)),
        "bars_after_filter": int(len(df_filtered)),
        "first_bar_ts": df_filtered["ts"].iloc[0].isoformat() if len(df_filtered) else None,
        "last_bar_ts": df_filtered["ts"].iloc[-1].isoformat() if len(df_filtered) else None,
        "close_sum": round(float(df_filtered["close"].sum()), 4),
        "volume_sum": int(df_filtered["volume"].sum()),
        "calendar_filter": "regular_hours_daily",
        "raw_file_hash": hashlib.sha256(raw_bytes).hexdigest(),
    }


# ── Backtest engine ────────────────────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    shares: int
    entry_price: float
    entry_ts: pd.Timestamp


@dataclass
class Trade:
    ts: pd.Timestamp
    symbol: str
    side: str
    shares: int
    price: float
    notional: float
    reason: str


@dataclass
class RoundTrip:
    symbol: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    shares: int
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str


@dataclass
class Engine:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    trades: List[Trade] = field(default_factory=list)
    round_trips: List[RoundTrip] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def equity(self, marks: Dict[str, float]) -> float:
        equity = self.cash
        for sym, pos in self.positions.items():
            mark = marks.get(sym, pos.entry_price)
            equity += pos.shares * mark
        return equity

    def execute_buy(self, ts: pd.Timestamp, symbol: str, fill_price: float,
                    target_notional: float, reason: str) -> bool:
        # apply slippage on buys
        effective = fill_price * (1.0 + SLIPPAGE)
        shares = int(math.floor(target_notional / effective))
        if shares <= 0:
            return False
        cost = shares * effective
        if cost > self.cash + 1e-6:
            shares = int(math.floor(self.cash / effective))
            if shares <= 0:
                return False
            cost = shares * effective
        self.cash -= cost
        self.positions[symbol] = Position(
            symbol=symbol, shares=shares, entry_price=effective, entry_ts=ts,
        )
        self.trades.append(Trade(
            ts=ts, symbol=symbol, side="BUY", shares=shares, price=effective,
            notional=cost, reason=reason,
        ))
        return True

    def execute_sell(self, ts: pd.Timestamp, symbol: str, fill_price: float,
                     reason: str) -> bool:
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return False
        effective = fill_price * (1.0 - SLIPPAGE)
        proceeds = pos.shares * effective
        self.cash += proceeds
        self.trades.append(Trade(
            ts=ts, symbol=symbol, side="SELL", shares=pos.shares, price=effective,
            notional=proceeds, reason=reason,
        ))
        pnl = (effective - pos.entry_price) * pos.shares
        ret = (effective / pos.entry_price) - 1.0
        bars_held = (ts.date() - pos.entry_ts.date()).days
        self.round_trips.append(RoundTrip(
            symbol=symbol, entry_ts=pos.entry_ts, exit_ts=ts, shares=pos.shares,
            entry_price=pos.entry_price, exit_price=effective, pnl=pnl,
            return_pct=ret, bars_held=bars_held, exit_reason=reason,
        ))
        return True


# ── Strategy: compute signal frame for one symbol ──────────────────────────────

def compute_signal_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["rsi_14"] = wilder_rsi(out["close"], RSI_PERIOD)
    out["sma_50"] = simple_sma(out["close"], SMA_PERIOD)
    out["avg_vol_20"] = out["volume"].rolling(VOL_PERIOD, min_periods=VOL_PERIOD).mean()
    out["above_sma50"] = out["close"] > out["sma_50"]
    out["near_sma50"] = out["close"] <= (out["sma_50"] * (1.0 + NEAR_SMA_PCT))
    out["vol_above_avg"] = out["volume"] > out["avg_vol_20"]
    if REQUIRE_UPTREND:
        out["entry_signal"] = (
            (out["rsi_14"] < RSI_BUY)
            & out["above_sma50"]
            & (out["near_sma50"] | out["vol_above_avg"])
        )
    else:
        out["entry_signal"] = out["rsi_14"] < RSI_BUY
    out["exit_signal"] = out["rsi_14"] > RSI_SELL
    return out


# ── Main backtest loop ─────────────────────────────────────────────────────────

def run_backtest(frames: Dict[str, pd.DataFrame]) -> tuple[Engine, pd.DataFrame]:
    engine = Engine(cash=INITIAL_CASH)

    # Build a unified trading-day index across all symbols (intersection: every
    # symbol must have a bar on day T to evaluate signals + fills on T+1).
    common_days = None
    for sym, df in frames.items():
        days = pd.DatetimeIndex(df["ts"].dt.normalize().unique())
        common_days = days if common_days is None else common_days.intersection(days)
    common_days = common_days.sort_values()

    # Restrict to backtest window (signals only fire inside [START, END])
    in_window = (common_days >= START) & (common_days <= END)
    window_days = common_days[in_window]

    # Per-symbol lookup table indexed by normalized day
    per_sym: Dict[str, pd.DataFrame] = {}
    for sym, df in frames.items():
        sigs = compute_signal_frame(df)
        sigs["day"] = sigs["ts"].dt.normalize()
        sigs = sigs.set_index("day", drop=False)
        per_sym[sym] = sigs

    equity_records: List[dict] = []
    pending_buys: List[tuple[str, str]] = []     # (symbol, reason) to execute at T+1 open
    pending_sells: List[tuple[str, str]] = []    # (symbol, reason)

    for i, day in enumerate(window_days):
        # 1. Execute pending fills using THIS day's open (fills from signals at T-1 close).
        if i > 0:
            for sym, reason in pending_sells:
                sigs = per_sym[sym]
                if day not in sigs.index:
                    engine.warnings.append(
                        f"sell skipped: no bar for {sym} on {day.date()}"
                    )
                    continue
                fill_px = float(sigs.loc[day, "open"])
                engine.execute_sell(sigs.loc[day, "ts"], sym, fill_px, reason)

            # buy fills come AFTER sells so freed-up cash can fund them
            slots_available = MAX_POSITIONS - len(engine.positions)
            buys_to_run = pending_buys[:slots_available]
            if pending_buys and slots_available > 0:
                target_notional_each = engine.cash / max(slots_available, 1)
            for sym, reason in buys_to_run:
                sigs = per_sym[sym]
                if day not in sigs.index:
                    engine.warnings.append(
                        f"buy skipped: no bar for {sym} on {day.date()}"
                    )
                    continue
                fill_px = float(sigs.loc[day, "open"])
                engine.execute_buy(sigs.loc[day, "ts"], sym, fill_px,
                                   target_notional_each, reason)
            pending_buys = []
            pending_sells = []

        # 2. Mark equity at end of THIS day (use close).
        marks = {}
        for sym, sigs in per_sym.items():
            if day in sigs.index:
                marks[sym] = float(sigs.loc[day, "close"])
        eq = engine.equity(marks)
        equity_records.append({
            "ts": day,
            "cash": round(engine.cash, 2),
            "equity": round(eq, 2),
            "open_positions": len(engine.positions),
            "exposure": round(eq - engine.cash, 2),
        })

        # 3. Evaluate signals on TODAY's close → schedule fills for tomorrow's open.
        for sym, sigs in per_sym.items():
            if day not in sigs.index:
                continue
            row = sigs.loc[day]
            held = sym in engine.positions

            # Skip if any indicator is still NaN (warmup not complete)
            if pd.isna(row["rsi_14"]) or pd.isna(row["sma_50"]) or pd.isna(row["avg_vol_20"]):
                continue

            if held and bool(row["exit_signal"]):
                pending_sells.append((sym, f"RSI {row['rsi_14']:.1f} > {RSI_SELL}"))
            elif (not held) and bool(row["entry_signal"]):
                pending_buys.append(
                    (sym, f"RSI {row['rsi_14']:.1f} < {RSI_BUY} & close>{row['sma_50']:.2f}")
                )

        # Sort pending buys by most-oversold RSI first (matches live engine)
        pending_buys.sort(
            key=lambda x: float(per_sym[x[0]].loc[day, "rsi_14"])
        )

    equity_df = pd.DataFrame(equity_records).set_index("ts")
    return engine, equity_df


# ── Benchmark: equal-weight buy-and-hold ───────────────────────────────────────

def run_benchmark(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    # Find first common day in [START, END]
    common_days = None
    for sym, df in frames.items():
        days = pd.DatetimeIndex(df["ts"].dt.normalize().unique())
        common_days = days if common_days is None else common_days.intersection(days)
    common_days = common_days.sort_values()
    in_window = (common_days >= START) & (common_days <= END)
    window_days = common_days[in_window]
    entry_day = window_days[0]
    next_day = window_days[1] if len(window_days) > 1 else window_days[0]

    # Equal-weight buy at next-open on day 1 (same fill model as strategy)
    per_sym_alloc = INITIAL_CASH / len(frames)
    holdings: Dict[str, int] = {}
    cash = INITIAL_CASH
    for sym, df in frames.items():
        row = df[df["ts"].dt.normalize() == next_day].iloc[0]
        fill_px = float(row["open"]) * (1.0 + SLIPPAGE)
        shares = int(math.floor(per_sym_alloc / fill_px))
        holdings[sym] = shares
        cash -= shares * fill_px

    records: List[dict] = []
    for day in window_days:
        equity = cash
        for sym, df in frames.items():
            day_rows = df[df["ts"].dt.normalize() == day]
            if day_rows.empty:
                continue
            equity += holdings[sym] * float(day_rows.iloc[0]["close"])
        records.append({"ts": day, "equity": round(equity, 2)})

    return pd.DataFrame(records).set_index("ts")


# ── Metrics ────────────────────────────────────────────────────────────────────

def compute_metrics(equity: pd.Series) -> dict:
    equity = equity.dropna()
    if len(equity) < 2:
        return {"total_return": 0.0, "ann_return": 0.0, "sharpe": 0.0,
                "max_drawdown": 0.0, "final_equity": float(equity.iloc[-1]) if len(equity) else 0.0,
                "trading_days": int(len(equity))}
    daily_ret = equity.pct_change().dropna()
    total_return = (equity.iloc[-1] / equity.iloc[0]) - 1.0
    trading_days = len(equity)
    ann_return = (1.0 + total_return) ** (252.0 / trading_days) - 1.0
    mean_d = daily_ret.mean()
    std_d = daily_ret.std(ddof=1)
    sharpe = (mean_d / std_d * math.sqrt(252.0)) if std_d > 0 else 0.0
    running_max = equity.cummax()
    drawdown = (equity / running_max) - 1.0
    max_dd = float(drawdown.min())
    return {
        "total_return": round(float(total_return), 4),
        "ann_return": round(float(ann_return), 4),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown": round(max_dd, 4),
        "final_equity": round(float(equity.iloc[-1]), 2),
        "trading_days": int(trading_days),
    }


def compute_trade_metrics(round_trips: List[RoundTrip]) -> dict:
    if not round_trips:
        return {"n_round_trips": 0, "hit_rate": 0.0, "profit_factor": 0.0,
                "avg_return_pct": 0.0, "avg_bars_held": 0}
    wins = [r for r in round_trips if r.pnl > 0]
    losses = [r for r in round_trips if r.pnl <= 0]
    sum_win = sum(r.pnl for r in wins)
    sum_loss = sum(r.pnl for r in losses)
    pf = float("inf") if not losses else (sum_win / abs(sum_loss) if sum_loss != 0 else float("inf"))
    return {
        "n_round_trips": len(round_trips),
        "hit_rate": round(len(wins) / len(round_trips), 4),
        "profit_factor": round(pf, 3) if pf != float("inf") else "inf",
        "avg_return_pct": round(
            float(np.mean([r.return_pct for r in round_trips])), 4
        ),
        "avg_bars_held": int(np.mean([r.bars_held for r in round_trips])),
    }


# ── Artifact writers ───────────────────────────────────────────────────────────

def write_artifacts(engine: Engine, equity_df: pd.DataFrame,
                    bench_df: pd.DataFrame, frames: Dict[str, pd.DataFrame],
                    filtered: Dict[str, pd.DataFrame]) -> dict:
    trades_df = pd.DataFrame([
        {"ts": t.ts.isoformat(), "symbol": t.symbol, "side": t.side,
         "shares": t.shares, "price": round(t.price, 4),
         "notional": round(t.notional, 2), "reason": t.reason}
        for t in engine.trades
    ])
    trades_df.to_csv(RUN_DIR / "trades.csv", index=False)

    rt_df = pd.DataFrame([
        {"symbol": r.symbol, "entry_ts": r.entry_ts.isoformat(),
         "exit_ts": r.exit_ts.isoformat(), "shares": r.shares,
         "entry_price": round(r.entry_price, 4),
         "exit_price": round(r.exit_price, 4),
         "pnl": round(r.pnl, 2), "return_pct": round(r.return_pct, 4),
         "bars_held": r.bars_held, "exit_reason": r.exit_reason}
        for r in engine.round_trips
    ])
    rt_df.to_csv(RUN_DIR / "round_trips.csv", index=False)

    equity_df.to_csv(RUN_DIR / "equity.csv")
    bench_df.to_csv(RUN_DIR / "benchmark_equity.csv")

    fp = {sym: fingerprint(sym, frames[sym], filtered[sym]) for sym in SYMBOLS}
    (RUN_DIR / "data_fingerprint.json").write_text(
        json.dumps(fp, indent=2, default=str), encoding="utf-8"
    )

    (RUN_DIR / "warnings.json").write_text(
        json.dumps({"warnings": engine.warnings}, indent=2), encoding="utf-8"
    )

    (RUN_DIR / "fee_source.json").write_text(json.dumps({
        "url": "https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf",
        "revision_date": None,
        "extracted_at": None,
        "modeled_categories": [],
        "excluded_categories": ["SEC", "FINRA TAF", "FINRA CAT", "ORF", "OCC",
                                "ADR pass-through", "commissions"],
        "note": "This run does not model trading-activity fees. Re-run with --model-fees to include them.",
    }, indent=2), encoding="utf-8")

    return fp


def write_report(strat_metrics: dict, bench_metrics: dict, trade_metrics: dict,
                 engine: Engine, fp: dict) -> None:
    first_trade = engine.trades[0] if engine.trades else None
    last_trade = engine.trades[-1] if engine.trades else None

    def fmt_trade(t: Optional[Trade]) -> str:
        if t is None:
            return "(none)"
        return f"{t.ts.date()} {t.side} {t.shares} {t.symbol} @ ${t.price:.4f} — {t.reason}"

    pct = lambda v: f"{v * 100:.2f}%"
    money = lambda v: f"${v:,.2f}"

    report = f"""# Backtest Report — DailyRSIStrategy (stock-equivalent)

## Performance vs Benchmark

|                   | Total Return | Ann. Return | Max Drawdown |  Sharpe | Final Equity |
|---                |          ---:|         ---:|          ---:|     ---:|          ---:|
| **Strategy**      | {pct(strat_metrics['total_return']):>11} | {pct(strat_metrics['ann_return']):>11} | {pct(strat_metrics['max_drawdown']):>12} | {strat_metrics['sharpe']:>7.3f} | {money(strat_metrics['final_equity']):>12} |
| Benchmark (EW B&H)| {pct(bench_metrics['total_return']):>11} | {pct(bench_metrics['ann_return']):>11} | {pct(bench_metrics['max_drawdown']):>12} | {bench_metrics['sharpe']:>7.3f} | {money(bench_metrics['final_equity']):>12} |

## Trade statistics

- Round trips: **{trade_metrics['n_round_trips']}**
- Hit rate: **{pct(trade_metrics['hit_rate'])}**
- Profit factor: **{trade_metrics['profit_factor']}**
- Avg return per round trip: **{pct(trade_metrics['avg_return_pct'])}**
- Avg bars held: **{trade_metrics['avg_bars_held']}**

## Strategy configuration

- Symbols: {', '.join(SYMBOLS)}
- Timeframe: {CONFIG['timeframe']} (feed={CONFIG['feed']}, adjustment={CONFIG['adjustment']})
- Window: {CONFIG['start']} → {CONFIG['end']}
- Initial cash: ${INITIAL_CASH:,.0f}
- Max concurrent positions: {MAX_POSITIONS}
- Fill model: next_open with {SLIPPAGE_BPS:.1f} bps slippage
- RSI period: {RSI_PERIOD}; buy < {RSI_BUY}, sell > {RSI_SELL}
- SMA period: {SMA_PERIOD}; near-SMA buffer: {NEAR_SMA_PCT * 100:.0f}%
- Volume confirmation window: {VOL_PERIOD}-day average

## First and last trade

- First: {fmt_trade(first_trade)}
- Last:  {fmt_trade(last_trade)}

## Assumptions and excluded layers

See [notes.md](notes.md) — option overlay, IV gate, earnings gate, LLM filter,
regime gate, risk gate, and option-specific exits are all out of scope. This
run measures only the technical RSI+trend signal on the underlying stock.

## Data fingerprint summary

"""
    for sym, info in fp.items():
        report += (f"- **{sym}**: {info['bars_after_filter']} bars after filter "
                   f"({info['first_bar_ts'][:10]} → {info['last_bar_ts'][:10]}), "
                   f"close_sum {info['close_sum']:.2f}, "
                   f"raw sha256 `{info['raw_file_hash'][:16]}...`\n")

    report += f"""
## Important disclosure

This backtest is a hypothetical historical simulation and does not represent
actual trading performance. Backtested results do not guarantee future results.
Results depend on market-data quality, data feed selection, corporate-action
handling, fees, slippage, liquidity, taxes, execution assumptions, and
implementation details. This material is for research and educational purposes
only and is not investment advice, a recommendation, an offer, or a solicitation
to buy or sell securities, options, cryptocurrencies, or any other financial
product. All investments involve risk and may lose value. Review Alpaca's
disclosures and agreements at https://alpaca.markets/disclosures.

Alpaca Brokerage Fee Schedule (not modeled in this run):
https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf

## Artifacts

- [trades.csv](trades.csv) — every executed fill
- [round_trips.csv](round_trips.csv) — realized entry/exit pairs
- [equity.csv](equity.csv) — daily strategy equity, cash, exposure
- [benchmark_equity.csv](benchmark_equity.csv) — daily benchmark equity
- [summary.json](summary.json) — machine-readable summary
- [data_fingerprint.json](data_fingerprint.json) — input-data hashes/counts
- [warnings.json](warnings.json) — non-fatal issues
- [fee_source.json](fee_source.json) — fee modeling provenance
- [strategy_spec.json](strategy_spec.json) — formalized rules
- [config.json](config.json) — run parameters
- [notes.md](notes.md) — assumptions and excluded layers
"""
    (RUN_DIR / "report.md").write_text(report, encoding="utf-8")


def write_summary(strat_metrics: dict, bench_metrics: dict,
                  trade_metrics: dict, engine: Engine, fp: dict) -> None:
    first_trade = engine.trades[0] if engine.trades else None
    last_trade = engine.trades[-1] if engine.trades else None

    def trade_dict(t: Optional[Trade]) -> dict:
        if t is None:
            return {}
        return {
            "ts": t.ts.isoformat(), "symbol": t.symbol, "side": t.side,
            "shares": t.shares, "price": round(t.price, 4),
            "notional": round(t.notional, 2), "reason": t.reason,
        }

    summary = {
        "strategy_name": "DailyRSIStrategy (stock-equivalent)",
        "start": CONFIG["start"],
        "end": CONFIG["end"],
        "symbols": SYMBOLS,
        "timeframe": CONFIG["timeframe"],
        "initial_cash": INITIAL_CASH,
        "metrics": {**strat_metrics, **trade_metrics},
        "benchmarks": {
            "equal_weight_buy_and_hold": bench_metrics,
        },
        "first_trade": trade_dict(first_trade),
        "last_trade": trade_dict(last_trade),
        "assumptions": [
            "next_open fill model with 5 bps slippage",
            "no commissions / no PDF-derived fees",
            "no dividends modeled",
            "no extended-hours bars",
            "no option overlay (technical signal on underlying)",
        ],
        "warnings": engine.warnings,
        "data_fingerprint": fp,
        "fee_source": json.loads((RUN_DIR / "fee_source.json").read_text()),
        "artifacts": {
            "trades": "trades.csv",
            "round_trips": "round_trips.csv",
            "equity": "equity.csv",
            "benchmark_equity": "benchmark_equity.csv",
            "report": "report.md",
            "notes": "notes.md",
            "strategy_spec": "strategy_spec.json",
        },
    }
    (RUN_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )


def main() -> None:
    frames_raw: Dict[str, pd.DataFrame] = {sym: load_bars(sym) for sym in SYMBOLS}

    # Subset to backtest window for fingerprinting (but compute signals on full series
    # because warmup happens before START).
    frames_filtered: Dict[str, pd.DataFrame] = {
        sym: df[(df["ts"] >= START) & (df["ts"] <= END)].reset_index(drop=True)
        for sym, df in frames_raw.items()
    }

    engine, equity_df = run_backtest(frames_raw)
    bench_df = run_benchmark(frames_raw)

    strat_metrics = compute_metrics(equity_df["equity"])
    bench_metrics = compute_metrics(bench_df["equity"])
    trade_metrics = compute_trade_metrics(engine.round_trips)

    fp = write_artifacts(engine, equity_df, bench_df, frames_raw, frames_filtered)
    write_report(strat_metrics, bench_metrics, trade_metrics, engine, fp)
    write_summary(strat_metrics, bench_metrics, trade_metrics, engine, fp)

    print("=" * 70)
    print(f"Strategy   : total_return {strat_metrics['total_return']:+.2%}, "
          f"ann {strat_metrics['ann_return']:+.2%}, sharpe {strat_metrics['sharpe']:.2f}, "
          f"max_dd {strat_metrics['max_drawdown']:.2%}")
    print(f"Benchmark  : total_return {bench_metrics['total_return']:+.2%}, "
          f"ann {bench_metrics['ann_return']:+.2%}, sharpe {bench_metrics['sharpe']:.2f}, "
          f"max_dd {bench_metrics['max_drawdown']:.2%}")
    print(f"Round trips: {trade_metrics['n_round_trips']}, "
          f"hit_rate {trade_metrics['hit_rate']:.2%}, "
          f"pf {trade_metrics['profit_factor']}, "
          f"avg_bars_held {trade_metrics['avg_bars_held']}")
    print(f"Final cash : ${engine.cash:,.2f}, open positions: {len(engine.positions)}")
    if engine.warnings:
        print(f"Warnings   : {len(engine.warnings)} (see warnings.json)")
    print("=" * 70)


if __name__ == "__main__":
    main()
