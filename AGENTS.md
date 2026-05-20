---
name: agents-registry
---

# TradeWiser Bot Agents

This file registers custom agents available in this workspace.

## agents

### trading-bot
- **Location**: `.github/agents/trading-bot.agent.md`
- **Purpose**: Specialized for autonomous trading bot development
- **Focus**: Trading logic, trading engine, AI advisor, Alpaca integration, autonomous trading features
- **Expertise**: Building/debugging trading strategies, optimizing trading algorithms, managing trading engine components
- **Use When**: Implementing or fixing trading features, enhancing AI capabilities, working with Alpaca integration

**Example Prompts:**
- "Implement a new momentum-based trading strategy"
- "Debug why autonomous trades aren't executing"
- "Add stop-loss logic to the trading engine"
- "Optimize the AI advisor to handle market volatility better"
- "Create integration tests for Alpaca order execution"
- "Analyze trading performance for the last week"
- "Deploy a new trading strategy to production"

### Default Agent
- **Purpose**: General-purpose development for all other tasks
- **Use When**: API development, config management, testing, deployment, infrastructure

## How to Use Custom Agents

In VS Code chat:
1. Type `@trading-bot` to mention the trading bot agent
2. Or reference it in your prompt: "Using the trading-bot agent, implement a RSI breakout strategy"
3. Or start a new chat and the agent will auto-activate when discussing trading logic

## Related Resources

- **File Instructions**: `.github/instructions/` — Auto-applied to specific file types
- **Prompts**: `.github/prompts/` — Reusable workflows (e.g., "Implement Trading Strategy")
- **Skills**: `.github/skills/` — Multi-step workflows (e.g., "Backtest Strategy")
