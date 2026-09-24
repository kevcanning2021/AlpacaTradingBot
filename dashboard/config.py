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
# regressions) keeps its own active_alerts in its own state file and was
# previously Telegram-only, invisible here. Its active_alerts entries now match
# watchdog's {first_seen, last_alert_at, message} shape so _load_active_alerts
# can read both the same way.
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
    'sofi': os.getenv('PEAK_PRICES_PATH_SOFI', '/opt/sofi-bot/peak_prices_state.json'),
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
    }.get(account_id)

# Each bot's own daily_fleet_audit.py (cron, independent of any Claude
# session -- see project notes) appends one entry per day to its own
# fleet_audit_log.json: a fresh backtest, a forward-test snapshot, watchlist/
# threshold drift detection, and how close each open position is to its
# stop. Read directly like the paths above -- same non-Alpaca-data pattern,
# one file per bot's own process.
FLEET_AUDIT_LOG_PATHS = {
    'main': os.getenv('FLEET_AUDIT_LOG_PATH_MAIN', '/opt/alpaca-bot/fleet_audit_log.json'),
    'sofi': os.getenv('FLEET_AUDIT_LOG_PATH_SOFI', '/opt/sofi-bot/fleet_audit_log.json'),
    'nova': os.getenv('FLEET_AUDIT_LOG_PATH_NOVA', '/opt/trading-2-0/fleet_audit_log.json'),
}

# Each bot's own record of CLOSED (round-trip) trades with realised P&L.
# Deliberately distinct from /api/accounts/{id}/orders, which returns Alpaca
# orders -- one side each, a buy OR a sell, carrying no profit/loss of their
# own, so "what did this trade make?" cannot be answered from orders alone.
# Keyed by dashboard account id (not bot name) since it's read per selected
# account. Nova is absent on purpose: it keeps round-trips in the sqlite
# journal at NOVA_JOURNAL_DB_PATH above, read separately.
CLOSED_TRADES_PATHS = {
    # 'prod' removed 2026-09-23 -- it now keeps a sqlite journal instead, see
    # journal_db_path(). Both readers check this dict FIRST and fall through
    # to the journal, so leaving a stale entry here would have quietly kept
    # serving the retired bot's frozen history as if it were current.
    'sofi': os.getenv('TRADE_HISTORY_PATH_SOFI', '/opt/sofi-bot/trade_history.json'),
}

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
FIND_TRADE_UNIT = os.getenv('FIND_TRADE_UNIT', 'nova-sofi-find-trade.service')
FIND_TRADE_STATE_PATH = os.getenv('FIND_TRADE_STATE_PATH',
                                   '/opt/nova-sofi/data/find_trade_state.json')
# Which dashboard account the button belongs to. Keyed like every other
# per-account path here so the route can reject a request aimed elsewhere
# rather than silently trading the wrong balance.
FIND_TRADE_ACCOUNT = os.getenv('FIND_TRADE_ACCOUNT', 'sofi')

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
