# TradeWiser Trading Policy

This document describes what the bot is **allowed to do**, what it is
**required to do**, and what **must stop it**. The policy is enforced by code
(not by convention) — every check named here corresponds to a runtime gate,
a configuration knob, or an audit table that exists today.

If you change a default in `app/core/config.py`, you are changing this policy.
Update this document in the same commit.

---

## 1. What the bot trades

- **Universe**: 24 curated underlyings in `app/services/watchlist_manager.py`
  (`EXPERT_PICKS`), across commodities, REITs, value, growth, index ETFs, and
  crypto proxies.
- **Instrument**: ATM call options expiring ~`OPTION_WEEKS_OUT` weeks out
  (default 4). Never the underlying stock. Never puts.
- **Order type**: market orders only. Single contract per trade
  (`TRADE_QUANTITY = 1`).
- **Venue**: Alpaca Markets. Paper-trading by default — switching to live
  requires an explicit change to `ALPACA_BASE_URL` in `.env`.

## 2. When a trade is allowed

A BUY is placed only when **all four** gates approve, in order:

| # | Gate | Source of truth | Where to look |
|---|---|---|---|
| 1 | **Strategy signal** | RSI(14) < `RSI_BUY_THRESHOLD` (35) AND price > 50-day SMA AND HV rank ≤ `IV_RANK_MAX` (50) AND days-to-earnings > `EARNINGS_DAYS_MIN` (7) | `app/services/trading_engine.py` (`get_daily_signal`) |
| 2 | **Regime gate** | VIX < `REGIME_VIX_PANIC_LEVEL` (35) AND SPY not in confirmed downtrend (price ≥ SMA50 or SMA50 ≥ SMA200) | `app/services/regime.py` |
| 3 | **AI advisor** | Stage-1 (Groq/Ollama) returns `BUY` with `confidence ≥ AI_MIN_CONFIDENCE` (0.65). If 0.65 ≤ confidence < 0.85 AND a smart-model key is configured, stage-2 (Claude / OpenAI) must also approve | `app/services/ai_advisor.py` |
| 4 | **Risk gate** | Symbol concentration ≤ `RISK_MAX_SYMBOL_CONCENTRATION_PCT` (25 %) AND today's P&L > -`RISK_MAX_DAILY_LOSS_DOLLARS` ($500) AND equity drawdown ≤ `RISK_MAX_DRAWDOWN_PCT` (15 %) | `app/services/risk_gate.py` |

A SELL passes the same chain except concentration is skipped (exits reduce
concentration by definition) and the daily-loss / drawdown halts do **not**
block SELLs by default (`RISK_HALT_BLOCKS_SELLS = false` — exits are always
allowed so the bot can defend capital). Setting `RISK_HALT_BLOCKS_SELLS = true`
changes this.

## 3. When the bot must not trade

The following conditions force `HOLD` regardless of any signal:

- **`AI_KILL_SWITCH = true`** — emergency stop. Every decision is forced to
  HOLD with confidence 0 and logged to `ai_decisions` with `outcome='kill_switch'`.
  Set this when you need to halt the bot without restarting the service.
- **Open AI circuit breaker** — after `AI_CIRCUIT_BREAKER_THRESHOLD` (5)
  consecutive LLM failures the breaker opens for
  `AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS` (120). All decisions are HOLD until
  a probe call succeeds.
- **LLM error and `AI_FAIL_CLOSED = true`** (default) — any timeout, network
  error, schema-validation failure, or 5xx returns HOLD. The bot **never**
  silently approves a trade the LLM did not approve. `AI_FAIL_CLOSED = false`
  exists for non-live testing only.
- **`MAX_POSITIONS = 5` reached** — no new BUYs until an existing position
  closes.

## 4. Untrusted input handling

News headlines are untrusted user data when they enter the LLM prompt.
Policy:

- Every headline passes through `ai_guardrails.sanitize_headlines` before
  reaching the LLM. Patterns like `ignore previous instructions`, `<system>`
  markers, role-tag exploits, and control characters cause the **entire
  headline to be dropped** (not redacted — redaction leaks the intent).
- At most `AI_MAX_NEWS_HEADLINES` (6) headlines reach any one prompt.
- Each headline is truncated to `AI_MAX_HEADLINE_CHARS` (160) post-sanitization.

## 5. Reliability requirements

Each LLM call:
- Has a hard wall-clock budget of `AI_REQUEST_TIMEOUT_SECONDS` (15 s).
- Is retried at most `AI_MAX_RETRIES` (2) times on transient failures with
  exponential backoff starting at `AI_RETRY_BACKOFF_SECONDS` (0.75).
- Must return JSON conforming to `ai_guardrails.AIDecision` — schema errors
  do **not** retry (they are not transient).
- Is persisted to the `ai_decisions` SQLite table with prompt hash, latency,
  attempts, provider, model, token counts, circuit state, outcome, and the
  full response payload. **An LLM call that is not audited did not happen.**

The stage-2 ensemble model only ever *upgrades* the decision. If stage-2
fails for any reason (no key, timeout, schema error) the stage-1 verdict
stands. Stage-2 cannot break a working bot.

## 6. Audit requirements

These four SQLite tables, all in `settings.AI_AUDIT_DB_PATH`, must contain a
row for every decision the bot makes:

| Table | What it records | Required for |
|---|---|---|
| `trade_audit` | Every BUY/SELL/dry-run attempt + result | P&L reconstruction, fill verification |
| `ai_decisions` | Every LLM call (stage 1 and stage 2 as separate rows) | Post-hoc evaluation of model quality, cost telemetry, debugging silent-HOLD episodes |
| `risk_events` | Every risk-gate evaluation, approved or blocked | Proving the gate actually fired when limits were breached |
| `account_snapshots` | Equity readings written by the risk gate on every evaluation | Rolling-peak drawdown calculation |

Audit failure must never block a trade — the gates log audit-write errors and
proceed. The risk floor is data integrity in audit, not availability of audit.

## 7. Operator obligations

The following are out of scope for the bot and are the operator's
responsibility:

- **Setting non-default thresholds before live trading.** Defaults
  (`RISK_MAX_DAILY_LOSS_DOLLARS = 500`, `RISK_MAX_DRAWDOWN_PCT = 15`,
  `RISK_MAX_SYMBOL_CONCENTRATION_PCT = 25`) are conservative paper-trading
  defaults. Calibrate them against your actual account size before flipping
  `ALPACA_BASE_URL` to live.
- **Setting `BOT_API_KEY` to a strong random value.** The service refuses to
  start if it is unset or equal to `dev-key-disabled`.
- **Monitoring `GET /health/broker`, `/trades/ai-status`, `/trades/risk-status`,
  and `/trades/market-regime`.** The bot will run silently when degraded; it
  is the operator's job to notice persistent circuit-open states, runaway
  token spend, drawdown trending toward the cap, or `/health/broker`
  returning `status: degraded`. `/health/broker` is unauthenticated and safe
  for an external uptime probe — alert on `status != "ok"` for more than a
  few cooldown windows.
- **Reviewing `ai_decisions` regularly.** A stage-2 model that consistently
  overrides stage-1 is signal — either stage-1 is miscalibrated or stage-2
  is overconfident. The audit log is the only way to know.

## 8. Change-control

Any commit that modifies one of the following must update this document in
the same change:

- Default values of `AI_*`, `RISK_*`, `REGIME_*`, or `ENSEMBLE_*` settings.
- The set of gates run during `start_trading_loop`'s BUY path.
- The audit-table schema in `app/services/utils.py`.
- The fail-closed / fail-open behavior of any gate.

If you cannot summarize the change in this document, the change is too large
to ship without splitting it.
