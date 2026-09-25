import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

ALPACA_PROD_API_KEY = os.getenv('ALPACA_PROD_API_KEY', '')
ALPACA_PROD_SECRET_KEY = os.getenv('ALPACA_PROD_SECRET_KEY', '')
ALPACA_TEST_API_KEY = os.getenv('ALPACA_TEST_API_KEY', '')
ALPACA_TEST_SECRET_KEY = os.getenv('ALPACA_TEST_SECRET_KEY', '')
ALPACA_SOFI_API_KEY = os.getenv('ALPACA_SOFI_API_KEY', '')
ALPACA_SOFI_SECRET_KEY = os.getenv('ALPACA_SOFI_SECRET_KEY', '')
ALPACA_TRADING2_API_KEY = os.getenv('ALPACA_TRADING2_API_KEY', '')
ALPACA_TRADING2_SECRET_KEY = os.getenv('ALPACA_TRADING2_SECRET_KEY', '')

# Path to the fleet watchdog's state file (/opt/alpaca-bot-test/watchdog.py,
# runs via root's crontab independent of any systemd service). Its
# active_alerts dict is the source for /api/issues below -- read directly
# rather than re-implementing service/log/position checks in the dashboard
# itself, so there's exactly one place that decides what counts as an issue.
WATCHDOG_STATE_PATH = os.getenv(
    'WATCHDOG_STATE_PATH', '/opt/alpaca-bot-test/watchdog_state.json'
)

# Same shape/purpose as WATCHDOG_STATE_PATH above, added 2026-09-01 -- Main's
# strategy_check.py (stuck-sell signal health, daily backtest/forward-test
# regressions) keeps its own active_alerts in its own state file, in watchdog's
# {first_seen, last_alert_at, message} shape so _load_active_alerts reads both
# the same way.
#
# NO LONGER MERGED INTO /api/issues, 2026-09-25. strategy_check.py belongs to
# the RETIRED alpaca-bot and evaluates ITS dual-signal Bollinger/RSI rules --
# which nothing runs any more. Its alerts are therefore judgements about a
# strategy that does not govern the positions they name, and it was raising
# exactly that: "TSLA has shown a SELL signal for 19 consecutive hourly checks
# while still held -- scheduler may not be closing it", about a position
# nova-main holds deliberately under Nova's rules, correctly journalled with a
# stop and sitting near its TARGET. A false alert claiming a position may be
# unprotected is worse than no alert, because it is the one kind the user must
# be able to trust. The other entry was 'crypto backtest expectancy went
# negative ... crypto trading is currently paused', last raised 2026-09-23 --
# true of the retired bot, meaningless for the live fleet.
#
# The capability itself is not lost: daily_fleet_audit.py covers backtest and
# forward-test regression, drift and stop distance for all three live bots, and
# watchdog.py covers stuck vetoes and service health. Kept here (not deleted)
# because the path is still the right one should that checker ever be rewritten
# against Nova's strategy.
STRATEGY_CHECK_STATE_PATH = os.getenv(
    'STRATEGY_CHECK_STATE_PATH', '/opt/alpaca-bot/strategy_check_state.json'
)

# Main/Sofi track a trailing peak price per open position (trader.py's
# _handle_trailing_stop) in their own {symbol: price} state file -- not
# Alpaca data, read directly like the paths above. Nova has no trailing-stop
# mechanism at all (its stop_price is fixed at entry, in its own sqlite
# journal instead -- see NOVA_JOURNAL_DB_PATH below), so there's
# deliberately no 'nova' entry here.
PEAK_PRICES_PATHS = {
    # 'prod' removed 2026-09-23: Main's account is now traded by nova-main
    # (Nova's code), which has no trailing-stop mechanism -- same reason Nova
    # was never in here. The old file still exists but stopped changing the
    # moment alpaca-bot was retired, and a frozen peak price rendered next to
    # a live position is worse than none.
    # 'sofi' removed 2026-09-24 for the same reason 'prod' was: the account is
    # now traded by Nova's code, which has no trailing-stop mechanism at all
    # (its stop is fixed at entry). The old file exists but stopped changing
    # when sofi-bot was retired, and a frozen peak price rendered beside a
    # live position is worse than none.
}

# Path to Nova's sqlite trade journal -- read directly (SELECT only) for each
# open trade's fixed stop_price/target_price, same non-Alpaca-data pattern
# as PEAK_PRICES_PATHS above.
NOVA_JOURNAL_DB_PATH = os.getenv('NOVA_JOURNAL_DB_PATH', '/opt/trading-2-0/data/trade_journal.db')

# Main's account has been traded by Nova's code since 2026-09-23 (alpaca-bot
# retired), so it now keeps the same sqlite journal rather than Main's old
# trade_history.json. Separate deployment, separate file: /opt/nova-main is a
# clone of /opt/trading-2-0 at the same commit, and the two journals must
# never be pointed at each other -- the whole point is comparing the same
# strategy at $50 and at $100k.
MAIN_JOURNAL_DB_PATH = os.getenv('MAIN_JOURNAL_DB_PATH', '/opt/nova-main/data/trade_journal.db')

# Sofi's account has been traded by nova-sofi since 2026-09-24 -- a plain
# clone of trading-2-0, same code and parameters, different account -- so it
# keeps the same sqlite journal rather than sofi-bot's trade_history.json.
# All three accounts now run one codebase; only the balances differ.
SOFI_JOURNAL_DB_PATH = os.getenv('SOFI_JOURNAL_DB_PATH', '/opt/nova-sofi/data/trade_journal.db')


def journal_db_path(account_id):
    """sqlite journal for accounts whose bot keeps one, else None.

    Deliberately a function, not a dict literal: the test-suite patches
    NOVA_JOURNAL_DB_PATH/MAIN_JOURNAL_DB_PATH as module attributes, and a dict
    built at import time would capture the original values and silently ignore
    the patch.
    """
    return {
        'trading2': NOVA_JOURNAL_DB_PATH,
        'prod': MAIN_JOURNAL_DB_PATH,
        'sofi': SOFI_JOURNAL_DB_PATH,
    }.get(account_id)

# Each bot's own daily_fleet_audit.py (cron, independent of any Claude
# session -- see project notes) appends one entry per day to its own
# fleet_audit_log.json: a fresh backtest, a forward-test snapshot, watchlist/
# threshold drift detection, and how close each open position is to its
# stop. Read directly like the paths above -- same non-Alpaca-data pattern,
# one file per bot's own process.
#
# Repointed 2026-09-25. 'main' and 'sofi' still named the RETIRED bots' repos,
# so this panel had been showing the dead scanners' audits under the live
# accounts since 2026-09-23/24: Main's forward test read "5 real closed trades,
# $+15.80" (alpaca-bot's lifetime record) when nova-main had 8 closed trades and
# -8.39R, and Sofi's read 2 trades and ADI/COST/UBER positions from a watchlist
# nova-sofi does not trade. CLOSED_TRADES_PATHS and PEAK_PRICES_PATHS were
# walked when those bots retired; these two were missed, and they fail in the
# reassuring direction -- a frozen audit of a dead bot reads as stability.
FLEET_AUDIT_LOG_PATHS = {
    'main': os.getenv('FLEET_AUDIT_LOG_PATH_MAIN', '/opt/nova-main/fleet_audit_log.json'),
    'sofi': os.getenv('FLEET_AUDIT_LOG_PATH_SOFI', '/opt/nova-sofi/fleet_audit_log.json'),
    'nova': os.getenv('FLEET_AUDIT_LOG_PATH_NOVA', '/opt/trading-2-0/fleet_audit_log.json'),
}

# Each bot's own record of CLOSED (round-trip) trades with realised P&L.
# Deliberately distinct from /api/accounts/{id}/orders, which returns Alpaca
# orders -- one side each, a buy OR a sell, carrying no profit/loss of their
# own, so "what did this trade make?" cannot be answered from orders alone.
# Keyed by dashboard account id (not bot name) since it's read per selected
# account. Nova is absent on purpose: it keeps round-trips in the sqlite
# journal at NOVA_JOURNAL_DB_PATH above, read separately.
# Now EMPTY. Both 'prod' (2026-09-23) and 'sofi' (2026-09-24) moved off
# trade_history.json when their accounts were handed to Nova's code, which
# keeps a sqlite journal instead -- see journal_db_path(). Both readers check
# this dict FIRST and fall through to the journal, so a stale entry here would
# quietly keep serving a retired bot's frozen history as if it were current.
#
# Kept as an empty dict rather than deleted: the fall-through in app.py and
# live_readiness.py reads it, and it is the right shape for any future bot
# that does keep a JSON history.
CLOSED_TRADES_PATHS = {}

# Each bot's own git repo, read only to date its most recent bug fix as a
# code-stability signal for the live-readiness panel. Keyed by dashboard
# account id like CLOSED_TRADES_PATHS above.
# Explicit record of when a real bug was last found in each bot's live
# path, used by the readiness panel's code-stability criterion. Explicit
# rather than inferred from git: see live_readiness._days_since_last_bug
# for why grepping commit messages was materially wrong.
# The fleet's first WRITE capability (2026-09-24): a button that runs one
# on-demand trading scan on the Sofi account. Nova's code, Nova's M1/M5/H1/H2
# timeframes, but triggered by a person instead of a poll loop.
#
# The unit is a systemd oneshot; the dashboard (unprivileged, alpacadash) may
# start it via a sudoers rule scoped to exactly this one command. It may NOT
# stop or restart anything, and cannot touch the always-on bots.
# On-demand scan ("Find a Trade"): a button that runs ONE scan now, on the same
# rules the always-on bots use. Keyed by dashboard account id, because more than
# one account has one -- Main since 2026-09-23, Sofi since 2026-09-24.
#
# This started as three scalars (FIND_TRADE_UNIT / _STATE_PATH / _ACCOUNT) built
# for exactly one account. Adding a second meant either duplicating all three or
# keying them; keyed is the version that does not rot, and the frontend already
# discovers which accounts have a button by asking the API rather than holding
# its own copy of the answer.
#
# Each unit is a systemd oneshot the dashboard may start via a sudoers rule
# scoped to that single command. It may NOT stop or restart anything.
FIND_TRADE_UNITS = {
    'prod': os.getenv('FIND_TRADE_UNIT_PROD', 'nova-main-find-trade.service'),
    'sofi': os.getenv('FIND_TRADE_UNIT_SOFI', 'nova-sofi-find-trade.service'),
}

FIND_TRADE_STATE_PATHS = {
    'prod': os.getenv('FIND_TRADE_STATE_PATH_PROD', '/opt/nova-main/data/find_trade_state.json'),
    'sofi': os.getenv('FIND_TRADE_STATE_PATH_SOFI', '/opt/nova-sofi/data/find_trade_state.json'),
}


def find_trade_unit(account_id):
    """The oneshot unit for this account's button, or None if it has none."""
    return FIND_TRADE_UNITS.get(account_id)


def find_trade_state_path(account_id):
    """Where that unit writes its result, or None if the account has no button."""
    return FIND_TRADE_STATE_PATHS.get(account_id)


BUG_HISTORY_PATH = os.getenv('BUG_HISTORY_PATH',
                              os.path.join(os.path.dirname(__file__), 'bug_history.json'))

# Mirrors trader.py's own constants (Main/Sofi's shared codebase) -- these
# aren't read from either bot's .env (STOP_LOSS_THRESHOLD/TRAILING_STOP_
# THRESHOLD are plain hardcoded constants there, not env-configurable), so
# there's no way to introspect them at runtime. Duplicated here and must be
# kept in sync by hand if trader.py's ever change, same convention already
# used for the research-agent keyword list across repos.
STOP_LOSS_THRESHOLD = 0.05
TRAILING_STOP_THRESHOLD = 0.08
CRYPTO_STOP_LOSS_THRESHOLD = 0.15
CRYPTO_TRAILING_STOP_THRESHOLD = 0.20

DASHBOARD_PASSWORD_HASH = os.getenv('DASHBOARD_PASSWORD_HASH', '')
DASHBOARD_SESSION_SECRET = os.getenv('DASHBOARD_SESSION_SECRET', '')

DASHBOARD_BIND_HOST = os.getenv('DASHBOARD_BIND_HOST', '127.0.0.1')
DASHBOARD_BIND_PORT = int(os.getenv('DASHBOARD_BIND_PORT', '8000'))
