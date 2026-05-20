# Workspace Customization Structure

This `.github/` folder contains customizations that guide development practices and enable specialized tools for TradeWiser Bot.

## Folder Organization

```
.github/
├── agents/                           # Custom agents
│   └── trading-bot.agent.md         # Specialized agent for autonomous trading
│
├── instructions/                     # File-level guidelines (auto-applied)
│   ├── trading-services.instructions.md      # For trading_engine.py, ai_advisor.py, alpaca_client.py
│   ├── routes-trades.instructions.md         # For routes/trades.py
│   └── test-trading.instructions.md          # For trading-related test files
│
├── prompts/                          # Reusable task-focused workflows
│   ├── implement-trading-strategy.prompt.md   # Guide: Implement new trading strategy
│   ├── debug-trade-execution.prompt.md        # Guide: Diagnose trading issues
│   └── analyze-trading-performance.prompt.md  # Guide: Review trading performance metrics
│
├── skills/                           # Complex multi-step workflows with assets
│   ├── backtest-strategy/
│   │   └── SKILL.md                 # Workflow: Backtest trading strategies
│   └── deploy-windows-service/
│       └── SKILL.md                 # Workflow: Build & deploy Windows Service
│
└── hooks/                            # Lifecycle automation & validation
    └── pre-commit.json              # Test coverage & code quality gates
```

## Quick Reference

### When to Use Each Customization

| Type | Use Case | Example |
|------|----------|---------|
| **Agent** | Specialized role for specific job | "Use trading-bot agent to implement momentum strategy" |
| **Instruction** | Always-on guidance for specific files | Auto-applied when editing `trading_engine.py` |
| **Prompt** | Task-focused workflow with clear steps | "/implement-trading-strategy" |
| **Skill** | Complex multi-step automation | "/backtest-strategy" or "/deploy-windows-service" |
| **Hook** | Enforce rules at lifecycle events | Block commits if test coverage < 80% |

## File Descriptions

### Agents
- **trading-bot.agent.md** — Autonomous trading expert. Specializes in trading engine, AI advisor, Alpaca integration, and autonomous trading features.

### Instructions
- **trading-services.instructions.md** — Guidelines for TradingEngine, AI Advisor, Alpaca Client. Covers principles, patterns, testing, and best practices.
- **routes-trades.instructions.md** — Standards for trade endpoints: authentication, validation, error handling, testing patterns.
- **test-trading.instructions.md** — Testing standards for trading modules. Required coverage levels, mocking patterns, test organization.

### Prompts (Type `/` in chat to find these)
- **implement-trading-strategy.prompt.md** — Step-by-step guide from strategy definition through testing and deployment.
- **debug-trade-execution.prompt.md** — Systematic diagnostic workflow for trading failures, with common issues and fixes.
- **analyze-trading-performance.prompt.md** — Performance analysis workflow with metrics, pattern identification, and optimization suggestions.

### Skills (Type `/` in chat to find these)
- **backtest-strategy/SKILL.md** — Automated workflow to backtest strategies, analyze performance, and validate before live deployment.
- **deploy-windows-service/SKILL.md** — Build executables, create MSI installer, deploy to production, and manage service lifecycle.

### Hooks
- **pre-commit.json** — Validates test coverage (80% minimum), warns about untested trading logic, auto-formats code.

## Root-Level Customization Files

- **AGENTS.md** — Registry of available custom agents and when to use them
- **copilot-instructions.md** — Workspace-wide development guidelines, standards, and best practices

## How to Use

### 1. Specialized Agent for Trading
```
@trading-bot Implement a RSI breakout strategy
```
Triggers the trading-bot agent which specializes in trading logic.

### 2. Auto-Applied File Instructions
When editing `app/services/trading_engine.py`, the `trading-services.instructions.md` guidelines are automatically included in context.

### 3. Task-Focused Prompts
```
/implement-trading-strategy
Strategy: Momentum Oscillator
Symbols: AAPL, MSFT, TSLA
```

### 4. Complex Workflows (Skills)
```
/backtest-strategy
Strategy: momentum
Symbols: AAPL, MSFT
Start: 2025-01-01
End: 2026-05-14
```

### 5. Git Hooks
Test coverage automatically validated on commit for trading modules.

## Key Features

✅ **Specialized Agent** — `trading-bot` knows trading logic, testing, and Alpaca integration  
✅ **File-Level Guidance** — Instructions auto-applied to trading files  
✅ **Reusable Prompts** — Common workflows with step-by-step guidance  
✅ **Complex Workflows** — Skills for multi-step tasks like backtesting and deployment  
✅ **Enforcement** — Hooks ensure quality gates (test coverage, code format)  
✅ **Documentation** — Comprehensive guidelines in each file  

## Discovery

To see all available customizations in VS Code:
- Type `/` in chat → See all prompts and skills
- Type `@` in chat → See available agents
- Files with `*.instructions.md` are auto-applied based on `applyTo` patterns
- Hooks are automatically enforced on git events

## Next Steps

1. **Try the trading-bot agent**: "@trading-bot Implement a new trading strategy"
2. **Use the implement prompt**: "/implement-trading-strategy"
3. **Run a backtest**: "/backtest-strategy"
4. **Deploy to Windows**: "/deploy-windows-service"

All customizations work together to provide specialized guidance for autonomous trading bot development!
