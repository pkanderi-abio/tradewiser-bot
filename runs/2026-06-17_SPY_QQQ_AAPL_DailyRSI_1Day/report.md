# Backtest Report — DailyRSIStrategy (stock-equivalent)

## Performance vs Benchmark

|                   | Total Return | Ann. Return | Max Drawdown |  Sharpe | Final Equity |
|---                |          ---:|         ---:|          ---:|     ---:|          ---:|
| **Strategy**      |      38.99% |      11.66% |      -21.04% |   0.871 |  $138,987.51 |
| Benchmark (EW B&H)|     108.82% |      27.98% |      -24.62% |   1.416 |  $206,735.23 |

## Trade statistics

- Round trips: **13**
- Hit rate: **92.31%**
- Profit factor: **29.608**
- Avg return per round trip: **7.88%**
- Avg bars held: **98**

## Strategy configuration

- Symbols: SPY, QQQ, AAPL
- Timeframe: 1Day (feed=sip, adjustment=split)
- Window: 2023-01-01 → 2025-12-31
- Initial cash: $100,000
- Max concurrent positions: 3
- Fill model: next_open with 5.0 bps slippage
- RSI period: 14; buy < 35.0, sell > 70.0
- SMA period: 50; near-SMA buffer: 5%
- Volume confirmation window: 20-day average

## First and last trade

- First: 2023-01-04 BUY 262 AAPL @ $126.9534 — RSI 32.3 < 35.0 & close>142.71
- Last:  2025-08-11 SELL 193 AAPL @ $227.8060 — RSI 73.7 > 70.0

## Assumptions and excluded layers

See [notes.md](notes.md) — option overlay, IV gate, earnings gate, LLM filter,
regime gate, risk gate, and option-specific exits are all out of scope. This
run measures only the technical RSI+trend signal on the underlying stock.

## Data fingerprint summary

- **SPY**: 751 bars after filter (2023-01-03 → 2025-12-30), close_sum 397586.74, raw sha256 `b6458e989db582e1...`
- **QQQ**: 751 bars after filter (2023-01-03 → 2025-12-30), close_sum 340215.45, raw sha256 `1dcad00b6175d8e0...`
- **AAPL**: 751 bars after filter (2023-01-03 → 2025-12-30), close_sum 153141.40, raw sha256 `518631057b7ef81e...`

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
