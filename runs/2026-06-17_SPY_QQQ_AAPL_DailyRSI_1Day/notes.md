# Run notes — 2026-06-17_SPY_QQQ_AAPL_DailyRSI_1Day

## Your original request

> Backtest the existing `DailyRSIStrategy` (the one wired into the live TradeWiser bot at `app/services/trading_engine.py`) using the Alpaca Skills Library Backtesting Skill.

## Confirmed strategy interpretation

This backtest exercises the **technical signal** behind DailyRSIStrategy on the underlying stocks. The live bot wraps the same signal with an ATM call-option overlay, an LLM confidence filter, and several portfolio-level gates; those layers are intentionally out of scope for v1.

- **Asset class**: stocks (Alpaca CLI v1 backtest support; options are out of scope).
- **Symbols**: SPY, QQQ, AAPL — the current `WATCHLIST` in `trading_engine.py`.
- **Timeframe / feed / adjustment**: 1Day, feed=sip, adjustment=split.
- **Date range**: 2023-01-01 → 2025-12-31 (3 years, covering the 2023 rally, 2024 advance, and 2025 chop / late-cycle volatility).
- **Initial cash**: $100,000.
- **Indicators**:
  - RSI(14), Wilder's smoothed (matches [reference.md#rsi-wilders-smoothed](../../.claude/skills/alpaca-trading-backtest/reference.md#rsi--wilders-smoothed))
  - SMA(50) of close, simple arithmetic mean
  - 20-day rolling average volume
- **Entry rule** (matches **production setting `STRATEGY_REQUIRE_UPTREND_FILTER=false`** as configured in the installed `.env`):
  1. `RSI_14[T] < 35`

  When `STRATEGY_REQUIRE_UPTREND_FILTER=true` (the code default but not the live default), the entry would additionally require `close[T] > SMA50[T]` AND (`close[T] <= SMA50[T] * 1.05` OR `volume[T] > avg_volume_20[T]`). A separate v1 run with the trend filter enabled produced **zero trades** because RSI<35 and price>SMA50 do not co-occur on SPY/QQQ/AAPL across this window — the oversold dips always dragged price below the 50-day SMA. That zero-trade outcome is itself a useful finding: the trend filter is structurally incompatible with the RSI-oversold trigger on large-cap indices.

- **Exit rule** (any may trigger on completed daily bar T close):
  1. `RSI_14[T] > 70`
- **Signal timing**: completed bar close (no peek at bar T+1).
- **Fill timing**: bar T+1 open.
- **Fill model**: `next_open` bar proxy. Quote-aware fills are not used because intraday quote data for 3 years of daily backtest adds little for end-of-day signals at significant cost.
- **Friction**: 5 bps slippage on buys and sells. No explicit spread because SPY/QQQ/AAPL trade with sub-bp effective spreads at retail size.
- **Sizing**: equal-weight up to 3 concurrent positions. At signal time, target notional = available cash / open slots; shares = `floor(target / close[T])`. Whole shares only.
- **Cash handling**: idle cash earns 0%. No margin, no shorting.
- **Benchmark**: equal-weight buy-and-hold of SPY, QQQ, AAPL — no rebalancing, same fill model and friction as the strategy on entry only.

## Inferred or defaulted assumptions

These were not specified in your request; I chose defaults consistent with the skill's guidance and the live strategy's spirit:

- Max concurrent positions: **3** (live bot uses `MAX_POSITIONS=5` but the watchlist here only has 3 names; setting `max=3` lets every signal participate).
- Signal trigger uses **strict `<`** on RSI buy and **strict `>`** on RSI sell to match the live code.
- Volume confirmation uses **today's bar** vs the 20-day rolling average (matches the live `get_daily_signal()`).
- 5 bps slippage chosen for liquid ETFs/large-caps; the live bot's option fills carry a much higher implicit spread, but that's out of scope here.
- Warmup is **65 bars** (max of RSI period + 1 and SMA period + 1 with a comfort buffer) — no signals are emitted before both indicators are well-defined.

## Excluded layers from the live strategy

| Layer | Why excluded |
|---|---|
| ATM call-option overlay | Skill v1 does not support options. Backtesting options requires historical IV, greeks, and contract-by-contract pricing we don't have via Alpaca daily CLI. The technical signal is what's being validated here. |
| HV-rank IV gate (> 50% skip) | Live bot uses 30-day rolling HV rank as a proxy for option pricing. Irrelevant when the trade is the underlying. |
| Earnings gate (skip if ≤ 7 days) | Requires separate earnings calendar feed (live bot uses yfinance). Not modeled v1. |
| LLM confidence filter (Groq/Ollama) | Out of scope for a deterministic backtest. Stage-2 ensemble likewise. |
| Regime gate (VIX panic, SPY/QQQ trend) | Macro overlay; assessed separately. |
| Risk gate (concentration / daily loss / drawdown) | Portfolio-level — would need to be re-simulated against the backtest equity curve, not the live account. |
| Option +60% profit target, -30% stop, trailing stop | These are option-mark exits, not underlying-price exits. Meaningless on stock. |
| Days-before-expiry exit (3d) | No expiry on the underlying. |

A separate run could layer the earnings and regime gates onto this same dataset; the strategy_spec is structured so those can be added as additional rules in `entry.rules` without changing the fill model.

## Run considerations resolved

- **Order simulation**: market-on-open of the next session after a signal closes.
- **Quote-aware vs bar-proxy fills**: bar proxy (`next_open`). Documented above.
- **Dividends**: not modeled. The bot's live signal is purely price/volume; dividend-paying ex-days don't shift RSI materially for these tickers.
- **Splits / reverse splits**: handled by Alpaca's split-adjusted feed.
- **Execution friction**: 5 bps slippage; no commission (Alpaca commission-free for these symbols).
- **Trading-activity fees**: not modeled in this run. See "Fee schedule" disclosure below.
- **Market hours**: regular hours only; no extended-hours bars.
- **Calendar**: Alpaca calendar implicit in daily-bar timestamps; no explicit holiday filter needed at 1Day resolution.
- **Benchmark choice**: equal-weight buy-and-hold of the same three names. Same fill assumptions.
- **Look-ahead bias**: avoided — every signal uses completed bar T close; every fill is at bar T+1 open.
- **Survivorship bias**: minimal (SPY/QQQ are index ETFs; AAPL has been continuously listed throughout the window).
- **Out-of-sample / walk-forward**: not performed in v1. A follow-up run can hold out 2025 and retune on 2023–2024 if you want parameter sensitivity.
- **Overfitting risk**: thresholds (35 / 70) are the live bot's hard-coded constants — they were **not** tuned for this dataset. Acceptable.

## Important disclosure

This backtest is a hypothetical historical simulation and does not represent actual trading performance. Backtested results do not guarantee future results. Results depend on market-data quality, data feed selection, corporate-action handling, fees, slippage, liquidity, taxes, execution assumptions, and implementation details. This material is for research and educational purposes only and is not investment advice, a recommendation, an offer, or a solicitation to buy or sell securities, options, cryptocurrencies, or any other financial product. All investments involve risk and may lose value. Review Alpaca's disclosures and agreements at [alpaca.markets/disclosures](https://alpaca.markets/disclosures).

## Fee schedule reference (for traceability)

Alpaca Brokerage Fee Schedule PDF (not modeled in this run; recorded for traceability):

```
https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf
```

If you want a follow-up run that models SEC / FINRA TAF / CAT / ORF fees on each fill, ask and I'll extend `run.py` and write `fee_source.json` with the modeled categories.
