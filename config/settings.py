import os
from dotenv import load_dotenv

load_dotenv()

# Alpaca API Configuration
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY', '')
ALPACA_BASE_URL = 'https://paper-api.alpaca.markets/v2'
DATA_BASE_URL = 'https://data.alpaca.markets/v2'
CRYPTO_DATA_BASE_URL = 'https://data.alpaca.markets/v1beta3/crypto/us'

# Market Hours (ET)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

# Trading Parameters
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL_MINUTES', '60'))
STOP_LOSS_THRESHOLD = 0.05  # Adjust stops when position moves 5%
REENTRY_THRESHOLD = 0.05    # Re-enter when pullback is 5%
# Minimum time a position must have been open before a reentry can fire (both stock and
# crypto -- unlike the stop-loss/trailing-stop/reentry-threshold splits above, this isn't
# about volatility magnitude by asset class, it's about whether the tracked peak is old
# enough to mean anything, which applies the same way to both). Added 2026-08-06 after a
# real reentry fired on GOOGL ~2.5h after its original scan-buy, same trading session --
# position_peak_prices[symbol] is set to current_price on the very first check after
# entry, so an ordinary intraday dip right after a fresh fill looked identical to a real
# pullback from an established peak. Backtested against 300 real daily bars across both
# watchlists: every historical reentry in that window fired 7+ trading days after its
# entry (soonest: NVDA at 7 days), so this gate costs zero backtested expectancy at any
# value from a few hours up to several days -- 4h picked as comfortably clear of the
# observed 2.5h failure while still well below the shortest real legitimate gap seen.
MIN_REENTRY_AGE_HOURS = float(os.getenv('MIN_REENTRY_AGE_HOURS', '4'))
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

# Crypto Scanner (runs 24/7, independent of stock market hours — see scheduler.py)
CRYPTO_WATCHLIST = [s.strip() for s in os.getenv('CRYPTO_WATCHLIST', 'BTC/USD,ETH/USD').split(',') if s.strip()]
# Was 60. Tightened 2026-07-16 -- a stop-loss/trailing-stop breach between checks was
# caught late, at whatever price prevailed next, not at the threshold, and this now
# trades real money on the production account (not just the test account). No .env
# override on either service, so this default applies to both. 15 (not stock's 5)
# because crypto's stop-loss/trailing-stop are already wider (15%/20%) specifically
# to tolerate crypto's higher ordinary volatility -- doesn't need as tight a loop.
CRYPTO_CHECK_INTERVAL_MINUTES = int(os.getenv('CRYPTO_CHECK_INTERVAL_MINUTES', '15'))
CRYPTO_POSITION_SIZE_USD = float(os.getenv('CRYPTO_POSITION_SIZE_USD', '500'))
CRYPTO_MAX_POSITIONS = int(os.getenv('CRYPTO_MAX_POSITIONS', '2'))
# Separate from STOP_LOSS_THRESHOLD/TRAILING_STOP_THRESHOLD: those were backtested only
# against the stock watchlist. Backtested against 100 real BTC/USD & ETH/USD daily bars
# (2026-04-04 to 2026-07-12, 2026-07-13) — the stock-tuned 8% trailing-stop would have
# tripped on ordinary volatility alone in 50-58% of all possible 20-day holding windows
# (vs. NVDA's occasional false trip that motivated 8% for stocks in the first place).
# 20%/15% cuts that to 8-16% of windows, a comparable reduction to what 8% achieved over
# the stock-tuned 5% — still not zero false trips, same "cut most, not all" philosophy.
CRYPTO_STOP_LOSS_THRESHOLD = float(os.getenv('CRYPTO_STOP_LOSS_THRESHOLD', '0.15'))
CRYPTO_TRAILING_STOP_THRESHOLD = float(os.getenv('CRYPTO_TRAILING_STOP_THRESHOLD', '0.20'))
# _handle_reentry computes the identical peak-relative pullback statistic as the trailing
# stop above (just advisory instead of closing) — REENTRY_THRESHOLD (5%, stock-tuned) was
# the one asset-class-sensitive threshold in trader.py that hadn't gotten a crypto split
# yet, which mattered as of 2026-07-14 once crypto started trading on the production
# account. IMPORTANT: must stay strictly below CRYPTO_TRAILING_STOP_THRESHOLD, not equal
# to it — check_positions() runs stop-loss -> trailing-stop -> re-entry in that order and
# skips re-entry once a position is closed, so setting them equal (an oversight in the
# first version of this fix, same day) means the trailing stop always closes the position
# at the same pullback level before re-entry can ever fire, making the advisory dead code.
# 0.125 preserves the same ratio as the stock config (REENTRY_THRESHOLD/TRAILING_STOP_THRESHOLD
# = 5%/8% = 0.625, applied to crypto's 20%: 0.625 * 0.20 = 0.125) rather than a new guess.
CRYPTO_REENTRY_THRESHOLD = float(os.getenv('CRYPTO_REENTRY_THRESHOLD', '0.125'))

# WhatsApp Notifications (via CallMeBot) -- kept for reference/rollback, but the
# live notifier switched to Telegram 2026-08-04 after CallMeBot's free quota ran
# out. WHATSAPP_ENABLED should stay false on every deployed .env going forward.
WHATSAPP_ENABLED = os.getenv('WHATSAPP_ENABLED', 'false').lower() == 'true'
WHATSAPP_PHONE = os.getenv('WHATSAPP_PHONE', '')    # International format without +, e.g. 27831234567
WHATSAPP_APIKEY = os.getenv('WHATSAPP_APIKEY', '')  # API key received from CallMeBot

# Telegram Notifications (via the Telegram Bot API) -- the active notifier as of
# 2026-08-04. Free, no message quota.
TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'false').lower() == 'true'
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')  # from @BotFather
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')      # your personal chat ID with the bot

# Research agent (Claude + web search veto on buy signals) -- ported from
# Mini/agents/research_agent.py, wired into trader.py.scan_and_execute()
# 2026-08-28. Fails open on any error; never blocks a trade because the
# agent itself broke, only because it found a concrete reason to.
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
RESEARCH_AGENT_VETO_ENABLED = os.getenv('RESEARCH_AGENT_VETO_ENABLED', 'false').lower() == 'true'
RESEARCH_AGENT_MODEL = os.getenv('RESEARCH_AGENT_MODEL', 'claude-sonnet-5')

# Dual stock signal source (Bollinger Band(20,2) mean-reversion + EMA9/21, first-fire-
# wins, Bollinger preferred on same-day tie) -- ported from Mini/origin/master
# (scanner.py, commit 9c2566d) 2026-08-28. Backtested (22mo, 460 daily bars, walk-
# forward): 90 trades, +1.71%/trade, +35.89% total, positive on train (+18.25%) and
# holdout (+15.70%), leave-one-symbol-out robust. NEVER forward-tested live -- defaults
# False pending a real paper-account track record, same phased-rollout pattern as
# RESEARCH_AGENT_VETO_ENABLED. Stock-only: crypto is deliberately excluded (a crypto
# Bollinger variant backtested badly negative, -34.56%/-50.88% over 9mo BTC/ETH; crypto
# keeps its existing single-method EMA9/21 regardless of this flag). Drought fallback
# (narrow-band bounce after 10+ flat trading days) intentionally NOT ported -- n=1
# backtest evidence, deferred to a separate follow-up once this has live results.
DUAL_SIGNAL_BOLLINGER_ENABLED = os.getenv('DUAL_SIGNAL_BOLLINGER_ENABLED', 'false').lower() == 'true'

# Paused 2026-09-01: strategy_check.py's daily re-backtest of the crypto EMA9/21
# strategy has come back negative for 3 straight days (-1.49%, -1.49%, -1.03%/trade,
# ~27% win rate). No crypto position was open when this was flipped. Gates new BUYs
# only -- existing stop-loss/trailing-stop/sell logic is untouched, so any future
# held crypto position would still be exited normally. Reversible via .env + restart
# once the backtest turns positive again.
CRYPTO_TRADING_ENABLED = os.getenv('CRYPTO_TRADING_ENABLED', 'true').lower() == 'true'
