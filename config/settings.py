import os
from dotenv import load_dotenv

load_dotenv()

# Alpaca API Configuration
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY', '')
ALPACA_BASE_URL = 'https://paper-api.alpaca.markets/v2'
DATA_BASE_URL = 'https://data.alpaca.markets/v2'

# Market Hours (ET)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

# Trading Parameters
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL_MINUTES', '60'))
STOP_LOSS_THRESHOLD = 0.05  # Adjust stops when position moves 5%
REENTRY_THRESHOLD = 0.05    # Re-enter when pullback is 5%
# Separate from STOP_LOSS_THRESHOLD: a flat 5% pullback-from-peak triggered too often on
# normal volatility in an intact uptrend (backtested watchlist, 90 daily bars, 2026-07-10 —
# NVDA alone false-tripped 4x while EMA9>EMA21 and RSI<85), undercutting the RSI-85 change's
# goal of letting winners run. 8% cut most single-name false trips while still catching real
# breakdowns.
TRAILING_STOP_THRESHOLD = 0.08

# Timezone
TIMEZONE = 'US/Eastern'  # Market hours are always NYSE/NASDAQ hours (ET), regardless of server location
REPORT_TIMEZONE = 'Africa/Johannesburg'  # Used for displayed timestamps

# Position Management
ENABLE_STOP_LOSS_ADJUSTMENT = True
ENABLE_REENTRY = True
INITIAL_EQUITY = float(os.getenv('INITIAL_EQUITY', '100000'))

# Opportunity Scanner
WATCHLIST = [s.strip() for s in os.getenv('WATCHLIST', 'AAPL,MSFT,GOOGL,AMZN,NVDA,SPY,QQQ').split(',') if s.strip()]
POSITION_SIZE_USD = float(os.getenv('POSITION_SIZE_USD', '1000'))  # Dollar amount per new position
MAX_POSITIONS = int(os.getenv('MAX_POSITIONS', '5'))               # Max concurrent open positions

# WhatsApp Notifications (via CallMeBot)
WHATSAPP_ENABLED = os.getenv('WHATSAPP_ENABLED', 'false').lower() == 'true'
WHATSAPP_PHONE = os.getenv('WHATSAPP_PHONE', '')    # International format without +, e.g. 27831234567
WHATSAPP_APIKEY = os.getenv('WHATSAPP_APIKEY', '')  # API key received from CallMeBot

# Email Notifications
EMAIL_ENABLED = os.getenv('EMAIL_ENABLED', 'false').lower() == 'true'
EMAIL_SMTP_HOST = os.getenv('EMAIL_SMTP_HOST', 'smtp.gmail.com')
EMAIL_SMTP_PORT = int(os.getenv('EMAIL_SMTP_PORT', '587'))
EMAIL_SMTP_USER = os.getenv('EMAIL_SMTP_USER', '')
EMAIL_SMTP_PASSWORD = os.getenv('EMAIL_SMTP_PASSWORD', '')
EMAIL_FROM = os.getenv('EMAIL_FROM', '')
EMAIL_TO = [e.strip() for e in os.getenv('EMAIL_TO', '').split(',') if e.strip()]
