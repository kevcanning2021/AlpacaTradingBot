# Alpaca Trading Bot - Market Hours Scheduler

A Python-based trading bot that monitors your positions during market hours, automatically opens new positions on scanner signals, and provides recommendations for stop loss adjustments and re-entries.

## Features

- **Market Hours Monitoring**: Runs only during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)
- **Periodic Checks**: Checks positions every 60 minutes (once per hour)
- **Opportunity Scanner**: Scans a configurable watchlist for EMA9/21 crossover + RSI signals and automatically buys/sells on them (`scanner.py`, `trader.py: scan_and_execute`)
- **Stop Loss Management**: Alerts when positions move 5% in either direction
- **Re-entry Suggestions**: Recommends re-entries when positions pullback 5% from peak
- **Interactive CLI**: Easy-to-use command interface
- **Paper Trading Support**: Works with Alpaca paper trading accounts

## Installation

1. Create a virtual environment:
```bash
python -m venv venv
venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your `.env` file with your Alpaca credentials (already configured)

## Usage

1. Start the bot:
```bash
python main.py
```

2. In the CLI, type `/schedule start` to begin monitoring

3. Available commands:
   - `/schedule start` - Start the market hours scheduler
   - `/schedule stop` - Stop the scheduler
   - `/schedule status` - Show scheduler status
   - `/status` - Show current account and position status
   - `/check` - Manually trigger a position check
   - `/history [limit]` - Show last N checks (default: 10)
   - `/config show` - Show current configuration
   - `/config set <param> <value>` - Modify settings
   - `/order <symbol> <qty> <buy|sell>` - Place a test order
   - `/positions` - Show all open positions
   - `/help` - Show all commands
   - `/exit` - Exit the application

## Configuration

Edit `config/settings.py` (or set via `.env`) to customize:
- `CHECK_INTERVAL_MINUTES`: How often to check positions (default: 60)
- `STOP_LOSS_THRESHOLD`: Percentage threshold for the entry-anchored stop loss (default: 5%)
- `TRAILING_STOP_THRESHOLD`: Percentage pullback from peak price for the trailing stop (default: 8%)
- `REENTRY_THRESHOLD`: Percentage pullback for re-entry suggestions (default: 5%)
- `MARKET_OPEN_HOUR/MINUTE`: Market opening time (default: 9:30 AM ET)
- `MARKET_CLOSE_HOUR/MINUTE`: Market closing time (default: 4:00 PM ET)
- `WATCHLIST`: Comma-separated symbols the opportunity scanner scans (default: `AAPL,MSFT,GOOGL,AMZN,NVDA,SPY,QQQ`)
- `POSITION_SIZE_USD`: Dollar amount bought per new scanner position, as a notional (fractional-share) order — works at any account size and any share price (default: 1000)
- `MAX_POSITIONS`: Max concurrent open positions the scanner will hold (default: 5)
- `CRYPTO_WATCHLIST`: Comma-separated crypto pairs scanned 24/7, independent of stock market hours (default: `BTC/USD,ETH/USD`)
- `CRYPTO_CHECK_INTERVAL_MINUTES`: How often to run the crypto scan/position check, around the clock (default: 60)
- `CRYPTO_POSITION_SIZE_USD`: Dollar amount bought per new crypto position (default: 500)
- `CRYPTO_MAX_POSITIONS`: Max concurrent open crypto positions (default: 2, tracked separately from stock `MAX_POSITIONS`)
- `CRYPTO_STOP_LOSS_THRESHOLD`: Entry-anchored stop-loss threshold for crypto positions, separate from `STOP_LOSS_THRESHOLD` since crypto's ordinary volatility is far wider than stocks (default: 15%)
- `CRYPTO_TRAILING_STOP_THRESHOLD`: Peak-relative trailing-stop threshold for crypto positions, separate from `TRAILING_STOP_THRESHOLD` for the same reason (default: 20%)

## Architecture

- `main.py` - Entry point
- `cli.py` - Interactive command-line interface
- `scheduler.py` - APScheduler integration for market hours monitoring
- `scanner.py` - EMA9/21 crossover + RSI opportunity scanner
- `trader.py` - Core trading logic, position management, and scanner order execution
- `alpaca_client.py` - Alpaca API wrapper
- `config/settings.py` - Configuration parameters

## Example Workflow

```
> /schedule start
✓ Scheduler started. Position checks will run every 60 minutes during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)

> /status
Account Status:
  Status: ACTIVE
  Equity: $100000
  Buying Power: $400000
  Trading Blocked: False
  Open Positions: 1

  Positions:
    AAPL: 1 shares @ $185.50 (Entry: $185.00, P&L: $0.50 / 0.27%)

> /check
Running manual position check...

Position Check Report (2026-06-27T14:30:00):
  Account Equity: $100000
  Buying Power: $400000
  Positions Checked: 1

  No actions recommended

> /exit
✓ Scheduler stopped
Exiting...
```

## Notes

- The scheduler runs in the background and monitors positions during market hours
- The opportunity scanner (`scan_and_execute`) **automatically places buy/sell orders** on its signals with no manual approval step, subject to the guard conditions in `trader.py` (already-held symbol, `MAX_POSITIONS`, buying power). Re-entry suggestions are recommendations only, reviewed via the CLI — but as of 2026-07-09, the stop-loss threshold (`_handle_stop_loss`) **automatically closes the position** when it's breached, rather than just alerting.
- The bot uses Alpaca's paper trading API by default (set in `.env`)
- Position history is kept for the last 100 checks

## Notifications

Real trade opens/closes (scanner buys/sells, stop-loss/trailing-stop/re-entry triggers) and genuine operational errors (a close/order that failed) are sent via Telegram (`telegram_notifier.py`, using the [Telegram Bot API](https://core.telegram.org/bots/api)), set via `.env`. Purely informational events — a daily status report, or `_handle_reentry` analyzing a pullback and deciding not to act (`REENTRY_SKIPPED`) — don't notify; only real trades and errors do.

- `TELEGRAM_ENABLED=true`
- `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
- `TELEGRAM_CHAT_ID` — your personal chat ID with the bot

Switched from WhatsApp (via CallMeBot) to Telegram on 2026-08-04 after CallMeBot's free quota ran out. `whatsapp_notifier.py` still exists (kept for reference/rollback) but nothing instantiates it anymore — `WHATSAPP_*` settings currently have no effect. `email_notifier.py` (SMTP-based) also still exists in the repo but `trader.py` no longer instantiates it — `EMAIL_*` settings currently have no effect.

## Deployment

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for how this is deployed (systemd on a VPS) and its current limitations, and [LESSONS.md](LESSONS.md) for durable mistakes-to-never-repeat from this project's history.
