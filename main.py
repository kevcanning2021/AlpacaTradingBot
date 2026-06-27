#!/usr/bin/env python3
"""
Alpaca Trading Bot - Market Hours Scheduler

This bot monitors your trading positions during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)
and provides automated recommendations for:
- Stop loss adjustments (when positions move 5%)
- Re-entry opportunities (after 5% pullbacks)

Usage:
    python main.py

Commands:
    /schedule start     - Start monitoring during market hours
    /schedule stop      - Stop the scheduler
    /status            - Show account and position status
    /check             - Manually trigger a position check
    /history           - Show recent position checks
    /config show       - Show current configuration
    /help              - Show all available commands
"""

import sys
import os

# Add current directory to path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import TradingCLI


def main():
    try:
        cli = TradingCLI()
        cli.run()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
