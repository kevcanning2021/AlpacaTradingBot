# Known Limitations

## Repository visibility

This repo is public. No credentials are committed (`.env` is gitignored), but the
trading strategy logic and thresholds are visible to anyone. The repo was made
public because the cloud routine's GitHub connector couldn't be authorized for
private repo access in this account at setup time.

## Credential storage in the cloud routine

The Alpaca API key/secret and Gmail app password are stored in plaintext inside
the scheduled routine's prompt/environment config on claude.ai. Per Anthropic's
docs, there is no dedicated secrets store for cloud routines yet — anything in
an environment or routine config is visible to anyone who can edit it. Low risk
for a single-user account, but not a hardened secrets setup.

## Stateless cloud runs — re-entry and streak logic don't fire in the cloud

Each hourly cloud routine run starts a **fresh session** with no memory of
previous runs. This means:

- `STOP_LOSS_ALERT` / `STOP_LOSS_CANDIDATE` (based on entry price vs. current
  price) work correctly on every run, since they only need the current
  snapshot.
- `REENTRY_CANDIDATE` (`trader.py: _handle_reentry`) depends on
  `position_peak_prices`, which is only populated by previous calls to
  `check_positions()` in the same `TradingManager` instance. Since the cloud
  routine recreates `TradingManager` from scratch every run, peak prices never
  accumulate, so re-entry suggestions never trigger via the cloud routine.
- The win/loss-streak strategy adjustments in `adjust_strategy()` (tightening
  or loosening stops after 3 consecutive wins/losses) depend on
  `win_streak`/`loss_streak` state on `TradingManager`, which also resets every
  cloud run and therefore never reaches the threshold of 3.

Both of these features work as intended when run via the local
`scheduler.py` process (`python main.py` → `/schedule start`), since that
keeps one long-lived `TradingManager` instance in memory across checks during
market hours. The cloud routine is best understood as a stop-loss-only
hourly health check, not a full replacement for the local scheduler.