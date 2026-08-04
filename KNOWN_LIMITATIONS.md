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
integration. Its optional WhatsApp/CallMeBot alerting is disabled
(`WHATSAPP_ENABLED=false`, set 2026-08-04 alongside the main bot's
WhatsApp→Telegram switch) — it was the actual source of "analysis" chat
messages Kevin didn't want, not the main Python bot.

## `strategy_check.py` — hourly VPS cron, not the Strategy Review routine

Added 2026-07-16, root crontab on the VPS (`0 * * * *`, test account only,
`/opt/alpaca-bot-test`), logs to `/var/log/alpaca-strategy-check.log`.
Alert-only (Telegram, same cooldown pattern as `watchdog.py`) — never
trades, never edits thresholds. Hourly: re-runs the live scanner for a
signal-health check. Daily, after close: re-backtests `BUY_RSI_MAX`/
`SELL_RSI_MIN` against fresh bars. Building it surfaced a real gap between
how the strategy was backtested and how the live scanner computed signals
each check (undercooked EMA21 from too short a bar window) — found and
fixed same day (`scanner.py: SIGNAL_BAR_WINDOW`), see `STRATEGY.md`
"Automated monitoring" for the full investigation.

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

The Alpaca API key/secret, Gmail app password, and Telegram bot token
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
  **still in-memory only**, not persisted — a restart resets the streak
  count to 0. No longer drives any live behavior or notification as of
  2026-07-27 (the daily WhatsApp report that displayed them was removed;
  `adjust_strategy()`, the only thing that ever acted on them, was
  already disabled 2026-07-17) — still returned by `analyze_performance()`
  for `cli.py`/dashboard-style consumers, but that's the only remaining
  use.
- Streaks update only when a position actually closes with a realized
  P&L, not from the concurrent unrealized-P&L direction of whatever's
  open (fixed 2026-07-09 — two correlated positions dipping together used
  to count as a false 3-loss streak).

## `adjust_strategy()` — backtested and disabled, 2026-07-16

`trader.py: adjust_strategy()` directly reassigns the module-level
`settings.STOP_LOSS_THRESHOLD` (tighten after 3 wins, loosen after 3
losses) and `settings.REENTRY_THRESHOLD` (tighten on +5% equity, loosen on
-5%) — the one threshold-mutating path in this codebase that, until today,
had no backtest behind it (every other threshold is validated against
historical bars, see `STRATEGY.md`). A full-system backtest (real
entries/exits/stop-loss/trailing-stop, 300 daily bars, ~14 months,
test-account sizing) found it underperformed fixed thresholds (+6.66% vs
+7.65% total return) while firing often (17 threshold changes across 25
closed trades). `scheduler.py` no longer calls it; the method itself is
left intact in `trader.py`, not deleted, in case a more robust backtest
later justifies re-enabling it. See `STRATEGY.md` "Open items" for the
caveat that this is a single historical path, not an aggregated result.
