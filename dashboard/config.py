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

# Paths to each bot's own research agent decision log -- Main and Sofi got
# their own wiring on 2026-08-28 (separate ports, see BOT_REGISTRY.md),
# joining Nova (reinstated 2026-08-27). Three independent files, one per
# bot's own process, read directly rather than through ReadOnlyAlpacaClient
# since none of this is Alpaca data. Originally a single
# RESEARCH_AGENT_DECISIONS_PATH pointing at whichever bot currently had the
# feature; now always all three, merged in app.py and tagged with which bot
# each decision came from.
RESEARCH_AGENT_DECISIONS_PATHS = {
    'main': os.getenv('RESEARCH_AGENT_DECISIONS_PATH_MAIN', '/opt/alpaca-bot/agent_decisions_state.json'),
    'sofi': os.getenv('RESEARCH_AGENT_DECISIONS_PATH_SOFI', '/opt/sofi-bot/agent_decisions_state.json'),
    'nova': os.getenv('RESEARCH_AGENT_DECISIONS_PATH_NOVA', '/opt/trading-2-0/data/research_decisions.json'),
}

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
    'prod': os.getenv('PEAK_PRICES_PATH_MAIN', '/opt/alpaca-bot/peak_prices_state.json'),
    'sofi': os.getenv('PEAK_PRICES_PATH_SOFI', '/opt/sofi-bot/peak_prices_state.json'),
}

# Path to Nova's sqlite trade journal -- read directly (SELECT only) for each
# open trade's fixed stop_price/target_price, same non-Alpaca-data pattern
# as PEAK_PRICES_PATHS above.
NOVA_JOURNAL_DB_PATH = os.getenv('NOVA_JOURNAL_DB_PATH', '/opt/trading-2-0/data/trade_journal.db')

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
