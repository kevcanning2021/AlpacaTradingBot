# Known Limitations

## Deployment

The bot runs as a systemd service (`alpaca-bot.service`) on a DigitalOcean VPS,
executing `run_server.py`. This keeps one long-lived `TradingManager` instance
in memory for the life of the process, so peak-price tracking and win/loss
streaks accumulate correctly across checks during market hours.

The claude.ai scheduled routine used during initial setup has been disabled —
it ran each check in a fresh, stateless session, which meant re-entry and
streak-based logic (see below) never triggered. It's left in place but
disabled in case it's useful again later (e.g. as a redundant off-VPS check).

## Repository visibility

This repo is public. No credentials are committed (`.env` is gitignored), but
the trading strategy logic and thresholds are visible to anyone. The repo was
made public because the cloud routine's GitHub connector couldn't be
authorized for private repo access in this account at setup time.

## Credential storage

The Alpaca API key/secret and Gmail app password are stored in plaintext in
two places: the VPS's `/opt/alpaca-bot/.env` (root-only, `chmod 600`), and the
disabled claude.ai routine's config (no dedicated secrets store exists there
per Anthropic's docs — visible to anyone who can edit that routine). Low risk
for a single-user account, but not a hardened secrets setup. SSH access to the
VPS uses a dedicated deploy key (`alpaca_bot_deploy`), separate from the
personal key used for GitHub.

## Why state matters: re-entry and streak logic

- `STOP_LOSS_ALERT` / `STOP_LOSS_CANDIDATE` (based on entry price vs. current
  price) work correctly on any single check, stateless or not.
- `REENTRY_CANDIDATE` (`trader.py: _handle_reentry`) depends on
  `position_peak_prices`, which only accumulates across repeated calls to
  `check_positions()` on the same `TradingManager` instance.
- The win/loss-streak strategy adjustments in `adjust_strategy()` (tightening
  or loosening stops after 3 consecutive wins/losses) depend on
  `win_streak`/`loss_streak` state on `TradingManager`, which likewise only
  builds up across repeated checks on one instance.

Both now work as intended on the VPS, since `run_server.py` keeps a single
`TradingManager` alive for the life of the service.
