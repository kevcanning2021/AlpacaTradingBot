import os
from dotenv import load_dotenv

load_dotenv()

# Alpaca API Configuration
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY', '')
ALPACA_BASE_URL = 'https://paper-api.alpaca.markets/v2'

# Market Hours (ET)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

# Trading Parameters
CHECK_INTERVAL_MINUTES = 60  # Check positions every 60 minutes (once per hour)
STOP_LOSS_THRESHOLD = 0.05  # Adjust stops when position moves 5%
REENTRY_THRESHOLD = 0.05    # Re-enter when pullback is 5%

# Timezone
TIMEZONE = 'US/Eastern'

# Position Management
ENABLE_STOP_LOSS_ADJUSTMENT = True
ENABLE_REENTRY = True
INITIAL_EQUITY = float(os.getenv('INITIAL_EQUITY', '100000'))

# Email Notifications
EMAIL_ENABLED = os.getenv('EMAIL_ENABLED', 'false').lower() == 'true'
EMAIL_SMTP_HOST = os.getenv('EMAIL_SMTP_HOST', 'smtp.gmail.com')
EMAIL_SMTP_PORT = int(os.getenv('EMAIL_SMTP_PORT', '587'))
EMAIL_SMTP_USER = os.getenv('EMAIL_SMTP_USER', '')
EMAIL_SMTP_PASSWORD = os.getenv('EMAIL_SMTP_PASSWORD', '')
EMAIL_FROM = os.getenv('EMAIL_FROM', '')
EMAIL_TO = [e.strip() for e in os.getenv('EMAIL_TO', '').split(',') if e.strip()]
