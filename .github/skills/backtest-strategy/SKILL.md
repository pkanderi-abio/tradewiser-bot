---
name: backtest-strategy
description: "Automated workflow to backtest trading strategies against historical market data and validate performance before live deployment"
---

# Backtest Strategy Skill

This skill provides a structured workflow to backtest trading strategies using historical market data.

## What It Does

1. Retrieves historical OHLC data for specified symbols
2. Applies strategy logic to historical candles
3. Simulates trade execution with realistic slippage/commissions
4. Generates performance metrics and equity curve
5. Identifies optimal parameters via walk-forward analysis
6. Validates strategy robustness

## Prerequisites

- Strategy implemented in `app/services/ai_advisor.py`
- Historical market data available (Alpaca, local CSV, etc.)
- Trading engine and backtesting framework ready

## Workflow

### Step 1: Define Backtest Parameters
```
Strategy: [strategy_name]
Symbols: [AAPL, MSFT, TSLA, ...] or [all]
Start Date: [YYYY-MM-DD]
End Date: [YYYY-MM-DD]
Timeframe: [1m, 5m, 15m, 1H, 1D]
Initial Capital: [amount, default $100,000]
Commission: [%, default 0.001]
Slippage: [%, default 0.05]
```

### Step 2: Retrieve Historical Data
```bash
# Get data from Alpaca
python scripts/backtest.py \
  --strategy momentum \
  --symbols AAPL MSFT TSLA \
  --start 2025-01-01 \
  --end 2026-05-14 \
  --timeframe 1H \
  --download
```

### Step 3: Run Backtest
```bash
# Execute backtest
python scripts/backtest.py \
  --strategy momentum \
  --symbols AAPL MSFT TSLA \
  --start 2025-01-01 \
  --end 2026-05-14 \
  --timeframe 1H \
  --run
```

### Step 4: Analyze Results
```
Backtest Results:
=================
Total Return: 45.2%
Annual Return: 28.1%
Sharpe Ratio: 1.89
Max Drawdown: -12.3%
Win Rate: 58.2%
Profit Factor: 1.87

Trade Summary:
  Total Trades: 342
  Winning Trades: 199 (58.2%)
  Losing Trades: 143 (41.8%)
  Avg Winner: $342
  Avg Loser: -$185
  Largest Win: $2,145
  Largest Loss: -$892
```

### Step 5: Walk-Forward Optimization
```bash
# Test different parameter combinations
python scripts/backtest.py \
  --strategy momentum \
  --optimize \
  --param-ranges \
    period=14,21,28 \
    threshold_high=65,70,75 \
    threshold_low=25,30,35 \
  --walk-forward 6m
```

### Step 6: Robustness Validation
```
Monte Carlo Analysis:
  10,000 simulations of random trade sequence
  95% confidence interval: [28.3%, 67.5%]
  Probability of profit: 94.2%

Parameter Sensitivity:
  period +/- 5: Return variance ±2.1%
  threshold +/- 5: Return variance ±4.8%
  
Conclusion: Strategy is robust with moderate parameter sensitivity
```

### Step 7: Review & Approve
```
Backtest Summary Document:
- Strategy logic verified
- Performance acceptable (>40% annual return)
- Drawdown within limits (<-15%)
- Win rate satisfactory (>55%)
- Robustness validated
- Ready for live deployment ✓

Deployment Checklist:
  [x] Historical backtest passed
  [x] Parameter sensitivity checked
  [x] Forward-walk validation complete
  [x] Monte Carlo tested
  [x] Live market conditions suitable
  [ ] Approved by risk manager
  [ ] Position sizing configured
  [ ] Stop-loss/profit-taking set
  [ ] Monitoring alerts configured
```

## Backtest Framework Example

```python
# scripts/backtest.py
from datetime import datetime
from app.services.ai_advisor import get_strategy_signal
from app.services.alpaca_client import AlpacaClient

class BacktestEngine:
    def __init__(self, initial_capital=100000, commission=0.001, slippage=0.0005):
        self.capital = initial_capital
        self.equity = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.trades = []
        self.equity_curve = []
    
    def backtest_strategy(self, symbol, data, strategy_func):
        """Run backtest on historical data."""
        for i in range(100, len(data)):  # Start after 100 bars for indicators
            bar = data[i]
            signal = strategy_func(symbol, data[:i+1])
            
            if signal and signal['action'] == 'buy':
                entry_price = bar['close'] * (1 + self.slippage)
                # Execute trade logic...
                
        return self.calculate_metrics()
    
    def calculate_metrics(self):
        """Calculate performance metrics."""
        total_pnl = sum(t['pnl'] for t in self.trades)
        returns = (self.equity - 100000) / 100000
        sharpe = self.calculate_sharpe_ratio()
        max_dd = self.calculate_max_drawdown()
        
        return {
            'total_pnl': total_pnl,
            'returns': returns,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            'win_rate': len([t for t in self.trades if t['pnl'] > 0]) / len(self.trades),
            'trades': len(self.trades)
        }
```

## Output Files

After backtest completes:
- `backtests/[strategy]_[date].json` — Full backtest results
- `backtests/[strategy]_[date]_equity.csv` — Daily equity curve
- `backtests/[strategy]_[date]_trades.csv` — Trade-by-trade breakdown
- `backtests/[strategy]_[date]_report.html` — Visual report

## Common Backtest Pitfalls

❌ Lookahead bias (using future data)
❌ Not accounting for commission/slippage
❌ Overfitting to historical data
❌ Ignoring survivorship bias
❌ Testing only favorable market conditions
✅ Use out-of-sample validation
✅ Include realistic costs
✅ Test multiple market regimes
✅ Validate with walk-forward analysis

## Next Steps

After successful backtest:
1. Implement strategy in `ai_advisor.py` (if not already done)
2. Create unit tests based on backtest results
3. Run integration tests with live market data
4. Deploy to trading engine with monitoring
5. Track live performance vs. backtest expectations
