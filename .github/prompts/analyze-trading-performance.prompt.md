---
name: analyze-trading-performance
description: "Analyze trading performance metrics and identify optimization opportunities"
---

# Analyze Trading Performance

Use this prompt to review strategy performance and identify improvements.

## Parameters

- **Strategy**: Which strategy to analyze (or "all")
- **Timeframe**: Last day/week/month/custom date range
- **Metrics**: Focus areas (win rate, returns, drawdown, sharpe ratio, etc.)

## Analysis Workflow

### Step 1: Collect Trade Data
```python
trades = get_trades_in_range(start_date, end_date, strategy=strategy)
print(f"Total trades: {len(trades)}")
print(f"Date range: {start_date} to {end_date}")

# Organize by symbol
by_symbol = {}
for trade in trades:
    by_symbol.setdefault(trade.symbol, []).append(trade)
```

### Step 2: Calculate Performance Metrics
```python
# Overall performance
total_pnl = sum(t.profit_loss for t in trades)
num_winners = len([t for t in trades if t.profit_loss > 0])
num_losers = len([t for t in trades if t.profit_loss < 0])
win_rate = num_winners / len(trades) if trades else 0

avg_winner = sum(t.profit_loss for t in trades if t.profit_loss > 0) / num_winners if num_winners > 0 else 0
avg_loser = sum(t.profit_loss for t in trades if t.profit_loss < 0) / num_losers if num_losers > 0 else 0
profit_factor = abs(avg_winner * num_winners) / abs(avg_loser * num_losers) if num_losers > 0 else 0

print(f"Total P&L: ${total_pnl:.2f}")
print(f"Win Rate: {win_rate:.2%}")
print(f"Avg Winner: ${avg_winner:.2f}")
print(f"Avg Loser: ${avg_loser:.2f}")
print(f"Profit Factor: {profit_factor:.2f}")
```

### Step 3: Analyze by Symbol
```python
# Identify best and worst performers
performance = {}
for symbol, symbol_trades in by_symbol.items():
    pnl = sum(t.profit_loss for t in symbol_trades)
    win_rate = len([t for t in symbol_trades if t.profit_loss > 0]) / len(symbol_trades)
    performance[symbol] = {'pnl': pnl, 'win_rate': win_rate, 'trades': len(symbol_trades)}

# Sort and display
sorted_perf = sorted(performance.items(), key=lambda x: x[1]['pnl'], reverse=True)
print("Top 5 symbols:")
for symbol, stats in sorted_perf[:5]:
    print(f"  {symbol}: P&L=${stats['pnl']:.2f}, WR={stats['win_rate']:.1%}, Trades={stats['trades']}")
```

### Step 4: Identify Patterns
```python
# Time of day analysis
by_hour = {}
for trade in trades:
    hour = trade.entry_time.hour
    by_hour.setdefault(hour, []).append(trade.profit_loss)

for hour, pnls in sorted(by_hour.items()):
    avg_pnl = sum(pnls) / len(pnls)
    print(f"Hour {hour:02d}: Avg P&L=${avg_pnl:.2f} ({len(pnls)} trades)")

# Volatility impact
low_vol_trades = [t for t in trades if t.volatility < 0.5]
high_vol_trades = [t for t in trades if t.volatility >= 0.5]

low_vol_pnl = sum(t.profit_loss for t in low_vol_trades) / len(low_vol_trades) if low_vol_trades else 0
high_vol_pnl = sum(t.profit_loss for t in high_vol_trades) / len(high_vol_trades) if high_vol_trades else 0

print(f"Low volatility avg P&L: ${low_vol_pnl:.2f}")
print(f"High volatility avg P&L: ${high_vol_pnl:.2f}")
```

### Step 5: Drawdown Analysis
```python
# Calculate running P&L and maximum drawdown
running_pnl = 0
peak = 0
max_drawdown = 0
drawdowns = []

for trade in sorted(trades, key=lambda t: t.exit_time):
    running_pnl += trade.profit_loss
    if running_pnl > peak:
        peak = running_pnl
    drawdown = (running_pnl - peak) / peak if peak != 0 else 0
    max_drawdown = min(max_drawdown, drawdown)
    drawdowns.append(drawdown)

print(f"Maximum Drawdown: {max_drawdown:.2%}")
print(f"Recovery trades needed: {len([d for d in drawdowns if d < max_drawdown/2])}")
```

### Step 6: Generate Report
```
TRADING PERFORMANCE REPORT
=========================
Period: [start_date] to [end_date]
Strategy: [strategy_name]

Performance Summary:
  Total Trades: X
  Total P&L: $X
  Win Rate: X%
  Profit Factor: X
  Average Winner: $X
  Average Loser: $X
  Max Drawdown: X%

Top Symbols:
  1. SYMBOL: $X P&L, X% win rate
  2. SYMBOL: $X P&L, X% win rate
  ...

Time Analysis:
  Best hour: [hour] with $X avg P&L
  Worst hour: [hour] with $X avg P&L

Volatility Impact:
  Low vol trades: $X avg P&L
  High vol trades: $X avg P&L

Risk Analysis:
  Current equity: $X
  Largest win: $X
  Largest loss: $X
  Consecutive losses: X

Recommendations:
  1. [Improvement based on data]
  2. [Optimization opportunity]
  3. [Risk adjustment needed]
```

## Key Metrics Definitions

| Metric | Definition | Target |
|--------|-----------|--------|
| Win Rate | % of profitable trades | > 55% |
| Profit Factor | Gross Profit / Gross Loss | > 1.5 |
| Sharpe Ratio | Return / Risk | > 1.0 |
| Max Drawdown | Largest peak-to-trough decline | < -20% |
| Avg Trade | Average profit per trade | > 0 |
| Expectancy | Avg Win * Win% - Avg Loss * Loss% | > 0 |

## Optimization Opportunities

After analysis, consider:
- **Entry Filters**: Add market conditions to improve win rate
- **Exit Strategy**: Tighten stop-loss or improve profit-taking
- **Position Sizing**: Kelly Criterion or fixed fractional sizing
- **Time Filters**: Trade only best hours/days
- **Symbol Selection**: Focus on best performers
- **Volatility Adjustment**: Scale position size by vol
