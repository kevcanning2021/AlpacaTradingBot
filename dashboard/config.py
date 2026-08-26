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

# Path to the research agent's decision log (agents/state.py on the main
# AlpacaTradingBot codebase, Test account only -- see the dashboard
# extension plan). A separate service's file, read directly rather than
# through ReadOnlyAlpacaClient since it isn't Alpaca data. Confirmed on the
# real VPS layout that the dashboard's unprivileged user can already read
# this path (world-readable state file, traversable directory tree) --
# no permission change needed.
RESEARCH_AGENT_DECISIONS_PATH = os.getenv(
    'RESEARCH_AGENT_DECISIONS_PATH', '/opt/alpaca-bot-test/agent_decisions_state.json'
)

DASHBOARD_PASSWORD_HASH = os.getenv('DASHBOARD_PASSWORD_HASH', '')
DASHBOARD_SESSION_SECRET = os.getenv('DASHBOARD_SESSION_SECRET', '')

DASHBOARD_BIND_HOST = os.getenv('DASHBOARD_BIND_HOST', '127.0.0.1')
DASHBOARD_BIND_PORT = int(os.getenv('DASHBOARD_BIND_PORT', '8000'))
