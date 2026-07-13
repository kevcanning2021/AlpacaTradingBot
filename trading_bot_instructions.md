# Trading Bot Instructions

## 1. Project Goal
Build a **paper-trading-only** crypto bot that trades BTC/USD on Alpaca's
paper account (`PA31D0YL6NTN`), using a 9/21 EMA crossover on 5-minute
candles. Purpose is to validate the strategy live in a risk-free account
before any consideration of real money — this file is the contract for
what "done" and "safe" mean for that build.

## 2. Safety Rules
- **Never place a live (non-paper) order.** Every order path must target
  the paper API/account only. No config flag, env var, or code path may
  make live trading reachable without a separate, explicit, future
  decision by Kevin.
- **Never trade outside BTC/USD.** No auto-expanding the symbol list.
  Adding another symbol requires an explicit sign-off and its own
  backtest, not a config tweak.
- **Never self-adjust position size or remove/loosen a stop-loss.** No
  performance-based auto-tuning of risk parameters. Any change to sizing
  or stop thresholds is a human decision, reviewed like code.
- **No live build/backtest gate:** do not wire this bot to place its
  first paper order until the TradingView 9/21 EMA crossover backtest
  (in-sample + out-of-sample) has actually been run and the results
  reviewed. "No verified backtest yet" blocks going live in paper mode,
  not just a warning.

## 3. Strategy Rules
- **Asset:** BTC/USD only.
- **Venue:** Alpaca (paper account `PA31D0YL6NTN`).
- **Timeframe:** 5-minute candles.
- **Entry:** 9-period EMA crosses above 21-period EMA, only when flat.
- **Exit (signal):** 9-period EMA crosses below 21-period EMA.
- **Backtest source:** TradingView Pine Script v5 strategy (built
  2026-07-13, not yet run) — 12-month window, split 8mo in-sample /
  4mo out-of-sample. Results pending; must be reviewed before this
  strategy is considered validated.

## 4. Risk Rules
- **Position sizing:** 100% of paper-account equity per trade (matches
  the backtest assumption exactly).
- **Max concurrent positions:** 1 (no pyramiding — never add to an
  existing position on a fresh crossover signal).
- **Stop-loss:** close position if price falls 15% below entry price.
- **Trailing stop:** close position if price falls 20% below the peak
  price reached since entry.
- **Max drawdown kill-switch:** if total paper-account equity drops 25%
  from its value at bot start, halt all trading (no new entries) until
  Kevin manually reviews and restarts it.

## 5. Broker/MCP Rules
- All order placement and account/market-data reads go through the
  connected Alpaca MCP server — no separate hand-rolled API client for
  this bot.
- Credentials stay in the MCP server's own configuration — never pasted
  into chat, committed to the repo, or logged in plaintext.
- Before the bot's first real (paper) order placement, do a manual,
  explicit smoke-test order/close confirmation with Kevin — same
  precedent as the existing bot's crypto rollout.

## 6. Memory Rules
- **Two local files**, same pattern as the existing production bot:
  - A machine-readable **state file** (current position, entry price,
    peak price, win/loss streak, drawdown baseline) — read/written every
    check cycle, survives restarts.
  - A human-readable **lessons/log file** — append-only notes on
    anything surprising (false stop-outs, missed signals, bugs), meant
    to be read by a human, not parsed by the bot.

## 7. Definition of Done
- [ ] TradingView backtest run, in-sample and out-of-sample metrics
      reviewed and judged acceptable (not just profitable-looking —
      checked for overfitting per the gap between the two windows).
- [ ] Bot connects only to the Alpaca paper account via the MCP server;
      no live-trading code path exists.
- [ ] Entry/exit/stop-loss/trailing-stop/max-drawdown logic implemented
      exactly as specified above, matching the backtested rules.
- [ ] State file and lessons file created and updating correctly across
      at least one full restart.
- [ ] Terminal output includes: a heartbeat line every check cycle (even
      when no signal fires), an explicit line for every trade event
      (entry, exit, stop-loss, trailing stop, drawdown halt), and a
      full account snapshot (equity, cash, position, unrealized P&L)
      each cycle.
- [ ] A manual, explicit smoke-test order + close confirmed working on
      the paper account before calling the bot "live" in paper mode.
- [ ] Kevin has reviewed and confirmed this file before any build prompt
      is written.
