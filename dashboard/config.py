import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

ALPACA_PROD_API_KEY = os.getenv('ALPACA_PROD_API_KEY', '')
ALPACA_PROD_SECRET_KEY = os.getenv('ALPACA_PROD_SECRET_KEY', '')
ALPACA_TEST_API_KEY = os.getenv('ALPACA_TEST_API_KEY', '')
ALPACA_TEST_SECRET_KEY = os.getenv('ALPACA_TEST_SECRET_KEY', '')

DASHBOARD_PASSWORD_HASH = os.getenv('DASHBOARD_PASSWORD_HASH', '')
DASHBOARD_SESSION_SECRET = os.getenv('DASHBOARD_SESSION_SECRET', '')

DASHBOARD_BIND_HOST = os.getenv('DASHBOARD_BIND_HOST', '127.0.0.1')
DASHBOARD_BIND_PORT = int(os.getenv('DASHBOARD_BIND_PORT', '8000'))
