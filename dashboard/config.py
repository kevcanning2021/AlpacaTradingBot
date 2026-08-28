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

# Path to the research agent's decision log. Originally Test-account only
# (agents/state.py on the AlpacaTradingBot codebase); as of 2026-08-27 this
# points at Nova's own decisions file instead (bot/research_agent.py on the
# separate trading-2-0 codebase) since Mini/Test was retired and the research
# agent was reinstated for Nova specifically -- not Main or Sofi yet. A
# separate service's file, read directly rather than through
# ReadOnlyAlpacaClient since it isn't Alpaca data.
RESEARCH_AGENT_DECISIONS_PATH = os.getenv(
    'RESEARCH_AGENT_DECISIONS_PATH', '/opt/alpaca-bot-test/agent_decisions_state.json'
)

# Path to the fleet watchdog's state file (/opt/alpaca-bot-test/watchdog.py,
# runs via root's crontab independent of any systemd service). Its
# active_alerts dict is the source for /api/issues below -- read directly
# rather than re-implementing service/log/position checks in the dashboard
# itself, so there's exactly one place that decides what counts as an issue.
WATCHDOG_STATE_PATH = os.getenv(
    'WATCHDOG_STATE_PATH', '/opt/alpaca-bot-test/watchdog_state.json'
)

# Same active_alerts shape as WATCHDOG_STATE_PATH, written by the fleet
# review agent (/opt/fleet-review-agent, 2026-08-28) for its pending
# medium/high-risk proposals -- a separate file rather than sharing
# watchdog_state.json so the two independently-scheduled writers (15-min
# watchdog cron, 2-hourly review agent) can never race on the same file.
# /api/issues below merges both sources.
FLEET_REVIEW_STATE_PATH = os.getenv(
    'FLEET_REVIEW_STATE_PATH', '/opt/fleet-review-agent/fleet_review_state.json'
)

DASHBOARD_PASSWORD_HASH = os.getenv('DASHBOARD_PASSWORD_HASH', '')
DASHBOARD_SESSION_SECRET = os.getenv('DASHBOARD_SESSION_SECRET', '')

DASHBOARD_BIND_HOST = os.getenv('DASHBOARD_BIND_HOST', '127.0.0.1')
DASHBOARD_BIND_PORT = int(os.getenv('DASHBOARD_BIND_PORT', '8000'))
