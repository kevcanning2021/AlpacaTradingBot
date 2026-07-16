# Known Limitations

## Deployment

The bot runs as a systemd service (`alpaca-bot.service`) on a DigitalOcean
VPS, executing `run_server.py` from `/opt/alpaca-bot` — the production
paper account (~$100k equity). A second, independent instance —
`alpaca-bot-test.service`, from `/opt/alpaca-bot-test` — trials the same
code against a small paper account (currently ~$50; rotated to a fresh
Alpaca account 2026-07-14, `.env` sized accordingly —
`POSITION_SIZE_USD=10`, `CRYPTO_POSITION_SIZE_USD=15`). Small enough that
whole-share `qty` orders would round to 0 on most watchlist symbols, which
is why scanner buys use notional/fractional-dollar sizing instead (see
`trader.py: scan_and_execute` and `alpaca_client.py: create_order`). Each
is a separate git clone, venv, and `.env` — fully independent, no shared
state.

A third service, `alpaca-dashboard.service`, is a read-only monitoring PWA
(separate branch, not yet merged to `master`).

A separate order-less TypeScript bot (`crypto-paper-bot/`) also runs on
the VPS via root crontab (not systemd) — analysis-only, no broker
integration, reuses the same WhatsApp/CallMeBot env vars.

## `strategy_check.py` — hourly VPS cron, not the Strategy Review routine

Added 2026-07-16, root crontab on the VPS (`0 * * * *`, test account only,
`/opt/alpaca-bot-test`), logs to `/var/log/alpaca-strategy-check.log`.
Alert-only (WhatsApp, same cooldown pattern as `watchdog.py`) — never
trades, never edits thresholds. Hourly: re-runs the live scanner for a
signal-health check. Daily, after close: re-backtests `BUY_RSI_MAX`/
`SELL_RSI_MIN` against fresh bars. See `STRATEGY.md` "Automated
monitoring" for what it checks and a real methodology gap it surfaced
between how the strategy was backtested and how the live scanner actually
computes signals each check.

## Strategy Review — a scheduled cloud routine, not disabled

A Claude Code scheduled routine (`trig_...`, "Strategy Review -
AlpacaTradingBot") runs hourly against the test account's credentials,
re-reads the strategy code fresh each run, and reports live
positions/orders/findings — analysis-only, cannot edit code, commit, or
trade. Its cloud sandbox cannot reach the VPS or `data.alpaca.markets`
(network-blocked), so it can only reason from account/order data and the
repo's own code, not real price bars — see `STRATEGY.md` for how bar-based
verification is done instead (directly from the VPS). Its own prompt
carries a running list of what it's already confirmed, so it doesn't
re-investigate settled questions every run.

## Repository visibility

This repo is public. No credentials are committed (`.env` is gitignored),
but the trading strategy logic and thresholds are visible to anyone.

## Credential storage

The Alpaca API key/secret, Gmail app password, and CallMeBot WhatsApp key
are stored in plaintext in each service's own `.env` (VPS, root-only,
`chmod 600`) — no dedicated secrets manager. Low risk for a single-user
paper-trading account, but not a hardened setup. SSH access to the VPS
uses a dedicated deploy key (`alpaca_bot_deploy`), separate from the
personal key used for GitHub. The dev machine's local `.env` mirrors
whichever account is being actively worked on — running heavy local API
activity (e.g. backtesting via `get_bars`) shares rate limits with
whatever live service uses the same key, and has caused transient request
timeouts on the live service before (self-resolved, no lasting harm, but
worth avoiding overlap with market hours if doing a lot of local pulls).

## State that matters: peak prices, re-entry, and streaks

- `position_peak_prices` (trailing stop, re-entry pullback calculation)
  and `reentry_fired` (re-entry once-per-episode gating) are **persisted
  to disk** (`peak_prices_state.json`, `reentry_state.json`, gitignored) —
  survive a service restart. Added 2026-07-14 after confirming restarts
  had become frequent enough (multiple deploys per day) that the old
  "the process rarely restarts" assumption no longer held, and a restart
  was silently re-seeding every peak to the current price, discarding
  whatever gain the trailing stop was supposed to be protecting.
- `win_streak`/`loss_streak` (`trader.py: _record_trade_outcome`) are
  **still in-memory only**, not persisted — deliberately, since they feed
  `adjust_strategy()`'s runtime threshold mutation (see below), and fixing
  that needs its own decision, not a mechanical copy of the peak-price
  fix. A restart resets the streak count to 0.
- Streaks update only when a position actually closes with a realized
  P&L, not from the concurrent unrealized-P&L direction of whatever's
  open (fixed 2026-07-09 — two correlated positions dipping together used
  to count as a false 3-loss streak).

## `adjust_strategy()` mutates live thresholds with no backtest behind it

`trader.py: adjust_strategy()` — called every check cycle — directly
reassigns the module-level `settings.STOP_LOSS_THRESHOLD` (tighten after 3
wins, loosen after 3 losses) and `settings.REENTRY_THRESHOLD` (tighten on
+5% equity, loosen on -5%). Every other threshold in this codebase is
backed by a real backtest against historical bars (see `STRATEGY.md`) —
this one isn't. It can silently override a verified threshold mid-session
based on nothing but a streak, and its trigger is further weakened by
`win_streak`/`loss_streak` resetting on every restart (see above).
Flagged 2026-07-16, not yet disabled, backtested, or removed — open
decision.
