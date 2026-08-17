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

**Not currently running the same code.** As of 2026-08-14, the test
account's stock scanner uses Bollinger Band mean-reversion while
production's stays on the original EMA9/21 crossover — a deliberate,
temporary divergence pending the milestone-gated real-money decision
(see `STRATEGY.md`), not a deployment gap to close. Check `git log -1`
on each `/opt/alpaca-bot*` clone before assuming they're in sync; the
"same code, different account size" description above is the normal
state, not a guarantee that always holds.

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
signal-health check. Daily, after close: re-backtests each watchlist
against fresh bars using whatever signal logic that asset class actually
runs live — Bollinger Band mean-reversion for stocks, EMA9/21 crossover
for crypto (see `STRATEGY.md` for the 2026-08-14 split; this script has
its own standalone reimplementation of both, not a call into `scanner.py`,
so it has to be kept in lockstep by hand whenever the live logic changes —
see repo `LESSONS.md` #22). Building it originally surfaced a real gap
between how the strategy was backtested and how the live scanner computed
signals each check (undercooked EMA21 from too short a bar window) — found
and fixed same day (`scanner.py: SIGNAL_BAR_WINDOW`), see `STRATEGY.md`
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

## State that matters: peak prices, re-entry, position method, and streaks

- `position_peak_prices` (trailing stop, re-entry pullback calculation),
  `reentry_fired` (re-entry once-per-episode gating), (added 2026-08-06)
  `position_opened_at` (re-entry minimum-age gating), and (added
  2026-08-17) `position_methods` (which of the two dual stock signal
  sources — Bollinger or EMA9/21, see `STRATEGY.md` — opened a held
  position, so it's checked against the right exit rule) are **persisted
  to disk** (`peak_prices_state.json`, `reentry_state.json`,
  `position_opened_state.json`, `position_method_state.json`, all
  gitignored) — survive a service restart. `position_methods` is
  load-bearing, not just convenience: without it, a restart would lose
  track of which exit rule applies to an already-open position, a silent
  correctness bug (wrong exit logic), not just a missed optimization. Peak prices/reentry-fired added 2026-07-14 after confirming
  restarts had become frequent enough (multiple deploys per day) that the
  old "the process rarely restarts" assumption no longer held, and a
  restart was silently re-seeding every peak to the current price,
  discarding whatever gain the trailing stop was supposed to be
  protecting. `position_opened_at` is seeded to "now" the first time a
  symbol is observed with no recorded open time — for a position that
  predates this feature (or after any state-file loss), this
  conservatively treats it as freshly opened rather than leaving it
  permanently blocked from ever re-entering.
- **`reentry_fired` can stay latched for as long as a position stays open
  with no new peak, which can outlive an unrelated code change.** It only
  clears when the position sets a fresh high above its currently-tracked
  peak (`trader.py: check_positions`) — a real, observed case: the test
  account's AMZN re-entered once on 2026-08-05 (before `MIN_REENTRY_AGE_HOURS`
  existed), then spent the following week+ below that peak. Its
  `reentry_fired` flag has stayed `True` the entire time, so even though
  AMZN's pullback-from-peak has been well past `REENTRY_THRESHOLD` since
  2026-08-12 (RSI and age both otherwise qualifying), it structurally
  cannot fire another re-entry until it first rallies to a new high. This
  isn't a bug — "one re-entry per pullback episode" is the intended
  design — but it means a position's *current* pullback depth alone
  doesn't tell you whether a re-entry is actually imminent; check
  `reentry_state.json` for whether that symbol's flag is already set.
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
