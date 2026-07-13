# Trading Bot Instructions

## 1. Project Goal
Build a **paper-trading-only** crypto bot that trades BTC/USD and ETH/USD
on Alpaca's paper account, using a classic RSI(14) mean-reversion
strategy on hourly candles. Purpose is to validate the strategy live in
a risk-free account, and to learn from its own real mistakes instead of
repeating them. This file is the contract for what "done" and "safe"
mean for that build.

## 2. Safety Rules
- **Never place a live (non-paper) order.** Every order path must target
  the paper API/account only. No config flag, env var, or code path may
  make live trading reachable without a separate, explicit, future
  decision by Kevin.
- **Never trade outside BTC/USD and ETH/USD.** No auto-expanding the
  symbol list. Adding another symbol requires an explicit sign-off and
  its own backtest, not a config tweak.
- **Never self-adjust position size or remove/loosen a stop-loss.** No
  performance-based auto-tuning of risk parameters. Any change to sizing
  or stop thresholds is a human decision, reviewed like code.
- **Never repeat a trade setup that has already lost.** Before any BUY or
  SELL, the bot must check its own memory (see section 6) and skip a
  signal that matches a real prior loss on the same symbol/setup, or that
  its lessons file already warns about — logged clearly, not silently.
- **No live/backtest gate:** do not place this bot's first paper order
  until this exact RSI(14) 30/70 logic has actually been backtested
  against real historical BTC/USD and ETH/USD bars and the results
  reviewed. "No verified backtest yet" blocks going live in paper mode,
  not just a warning.

## 3. Strategy Rules
- **Assets:** BTC/USD and ETH/USD only — both may be held at once.
- **Venue:** Alpaca (paper account).
- **Timeframe:** 1-hour candles.
- **Entry:** RSI(14) drops below 30 (oversold), only when flat on that
  symbol.
- **Exit:** RSI(14) rises above 70 (overbought) — this is the only exit
  signal; no separate fixed take-profit.
- **Backtest source:** not yet run. Must be backtested against real
  historical hourly bars (same approach as the existing EMA9/21 bot's
  local backtests) before this file's Definition of Done is satisfied.

## 4. Risk Rules
- **Position sizing:** 25% of paper-account equity per trade. With both
  symbols tradeable at once, this caps total invested capital at 50%,
  leaving a 50% cash buffer.
- **Max concurrent positions:** 2 (one per symbol; no pyramiding — never
  add to an existing position on a fresh signal).
- **Stop-loss:** close position if price falls 15% below entry price.
- **Take profit:** none separate — the RSI > 70 exit signal is the only
  profit-taking mechanism.
- **Max drawdown kill-switch:** if total paper-account equity drops 25%
  from its value at bot start, halt all new entries (existing exits still
  fire) until Kevin manually reviews and restarts it.

## 5. Broker/MCP Rules
- All order placement and account/market-data reads go through the
  connected Alpaca MCP server — no separate hand-rolled API client for
  this bot.
- Credentials stay in the MCP server's own configuration — never pasted
  into chat, committed to the repo, or logged in plaintext.
- Before the bot's first real (paper) order placement, do a manual,
  explicit smoke-test order/close confirmation with Kevin — same
  precedent as the existing bots' rollouts.

## 6. Memory Rules
- **Two local files**, same pattern as the existing bots:
  - A machine-readable **state file** (current positions per symbol,
    entry price, peak price, win/loss streak, drawdown baseline) — read/
    written every check cycle, survives restarts.
  - A human-readable **lessons/log file** — append-only, plain-English
    notes on real losses and anything surprising (false stop-outs, missed
    signals, bugs).
- Before any BUY or SELL, both files must be consulted: has this
  symbol/setup lost before, does the lessons file warn about it, is this
  signal a repeat of a known bad trade? If yes: SKIP, log the reason
  clearly, no order placed. Never seed a fake loss or invent history —
  only real recorded outcomes may cause a skip.

## 7. Definition of Done
- [ ] RSI(14) 30/70 logic backtested against real historical BTC/USD and
      ETH/USD hourly bars, results reviewed (checked for overfitting, not
      just profitable-looking).
- [ ] Bot connects only to the Alpaca paper account via the MCP server;
      no live-trading code path exists.
- [ ] Entry/exit/stop-loss/max-drawdown logic implemented exactly as
      specified above, matching the backtested rules.
- [ ] State file and lessons file created and updating correctly across
      at least one full restart, and the skip-on-repeat-loss check
      verified working against a real recorded loss.
- [ ] Terminal output includes: a heartbeat line every check cycle (even
      when no signal fires), an explicit line for every trade event
      (entry, exit, stop-loss, SKIP, drawdown halt), and a full account
      snapshot (equity, cash, positions, unrealized P&L) each cycle.
- [ ] A manual, explicit smoke-test order + close confirmed working on
      the paper account before calling the bot "live" in paper mode.
- [ ] Kevin has reviewed and confirmed this file before any build prompt
      is written.

## 8. Backtest Results (2026-07-13) — SHELVED, not built

The RSI(14) mean-reversion strategy in section 3 was backtested with
`backtest.js` (real Binance BTCUSDT/ETHUSDT candles, no fabricated data)
against 8 combinations before a build prompt was ever written:

- **Hourly candles** (4000 bars, ~5.5 months, 70/30 in-sample/out-of-sample
  split), thresholds 30/70, 25/75, 20/80, 35/65: all net negative
  full-window (-4.95% to -14.59%). 25/75 and 20/80 additionally showed a
  textbook overfitting pattern — profitable in-sample, sharply negative
  out-of-sample — worse than an honestly-consistent loser.
- **Daily candles** (500 bars, ~16 months), same 4 threshold pairs: also
  net negative everywhere a signal fired (20/80 fired zero trades in the
  whole window — too strict to be informative). Trade counts were small
  (3-14 total), so these results are as much "not enough data" as
  "confirmed negative," but nothing pointed toward a real edge either.

**Decision: shelved.** 0 of 8 tested combinations cleared "reviewed and
judged acceptable, not just profitable-looking." Rather than keep
sweeping parameters on a small dataset (which stops being validation and
starts being overfitting search), this strategy is parked here as a
real, useful negative result. This file and `backtest.js` are kept for
the record. No bot code was written; no broker/MCP connection was ever
made.
