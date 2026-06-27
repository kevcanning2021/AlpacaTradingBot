# Alpaca Trading Bot - Market Hours Scheduler

A Python-based trading bot that monitors your positions during market hours and provides automated recommendations for stop loss adjustments and re-entries.

## Features

- **Market Hours Monitoring**: Runs only during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)
- **Periodic Checks**: Checks positions every 60 minutes (once per hour)
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

Edit `config/settings.py` to customize:
- `CHECK_INTERVAL_MINUTES`: How often to check positions (default: 60)
- `STOP_LOSS_THRESHOLD`: Percentage threshold for stop loss alerts (default: 5%)
- `REENTRY_THRESHOLD`: Percentage pullback for re-entry suggestions (default: 5%)
- `MARKET_OPEN_HOUR/MINUTE`: Market opening time (default: 9:30 AM ET)
- `MARKET_CLOSE_HOUR/MINUTE`: Market closing time (default: 4:00 PM ET)

## Architecture

- `main.py` - Entry point
- `cli.py` - Interactive command-line interface
- `scheduler.py` - APScheduler integration for market hours monitoring
- `trader.py` - Core trading logic and position management
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
- All recommendations are just that - you can manually review and execute orders via the CLI
- The bot uses Alpaca's paper trading API by default (set in `.env`)
- Position history is kept for the last 100 checks

## Email Notifications

You can enable email alerts for strategy changes by setting the SMTP values in `config/settings.py`:

- `EMAIL_ENABLED = True`
- `EMAIL_SMTP_HOST`
- `EMAIL_SMTP_PORT`
- `EMAIL_SMTP_USER`
- `EMAIL_SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

When a strategy adjustment occurs, the bot will send a breakdown email with:
- previous stop loss threshold
- previous re-entry threshold
- changes made
- rationale for the adjustment
