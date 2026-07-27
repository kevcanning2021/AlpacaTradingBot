import logging
from datetime import datetime
from typing import Dict
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pytz
from trader import TradingManager
from config.settings import (
    CHECK_INTERVAL_MINUTES,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    TIMEZONE,
    CRYPTO_WATCHLIST,
    CRYPTO_CHECK_INTERVAL_MINUTES,
    CRYPTO_POSITION_SIZE_USD,
    CRYPTO_MAX_POSITIONS,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MarketHoursScheduler:
    """Scheduler that runs trading checks during market hours"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.trading_manager = TradingManager()
        self.is_running = False
        self.last_check = None
        self.check_history = []
    
    def _is_market_open(self) -> bool:
        """Check if market is currently open (weekdays 9:30 AM - 4:00 PM ET)"""
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        
        # Only run on weekdays (0-4 = Mon-Fri)
        if now.weekday() > 4:
            return False
        
        market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
        market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
        
        return market_open <= now <= market_close
    
    def _check_positions_job(self):
        """Job that runs every CHECK_INTERVAL_MINUTES during market hours"""
        if not self._is_market_open():
            return
        
        logger.info("=" * 60)
        logger.info("Starting position check and opportunity scan...")

        try:
            # 1. Scan watchlist and execute any new buy/sell signals
            scan_report = self.trading_manager.scan_and_execute()
            if scan_report.get('executed'):
                logger.info(f"Scanner executed {len(scan_report['executed'])} trade(s):")
                for trade in scan_report['executed']:
                    qty_str = f"{trade['qty']} " if trade.get('qty') else ''
                    logger.info(f"  {trade['side'].upper()} {qty_str}{trade['symbol']} — {trade['reason']}")
            if scan_report.get('errors'):
                logger.warning(f"Scanner errors: {scan_report['errors']}")

            # 2. Review all open positions (including any just opened)
            report = self.trading_manager.check_positions()
            report['scan_report'] = scan_report
            self.last_check = report
            self.check_history.append(report)

            performance = self.trading_manager.analyze_performance()
            report['performance'] = performance

            # adjust_strategy() (win/loss-streak + equity-swing driven mutation of
            # STOP_LOSS_THRESHOLD/REENTRY_THRESHOLD) is deliberately NOT called here as
            # of 2026-07-16 -- a full-system backtest (real entries/exits/stops, 300
            # daily bars, ~14 months, test-account sizing) found it underperformed
            # fixed thresholds (+6.66% vs +7.65% total return) while firing often (17
            # threshold changes across 25 closed trades). See STRATEGY.md "Automated
            # monitoring" / trader.py: adjust_strategy() docstring. Method left intact,
            # not deleted -- Kevin may revisit with a more robust multi-window backtest.

            # Keep only last 100 checks
            if len(self.check_history) > 100:
                self.check_history.pop(0)

            logger.info(f"Check completed at {report.get('timestamp')}")
            if report.get('actions_taken'):
                logger.info(f"Actions: {len(report['actions_taken'])} recommendation(s)")
                for action in report['actions_taken']:
                    logger.info(f"  - {action.get('action')}: {action.get('symbol')} - {action.get('recommendation')}")

            if report.get('errors'):
                logger.warning(f"Errors: {report['errors']}")

        except Exception as e:
            logger.error(f"Error in position check job: {e}")

    def _check_crypto_job(self):
        """Job that runs every CRYPTO_CHECK_INTERVAL_MINUTES, around the clock — crypto
        trades 24/7 so this job is not gated by _is_market_open()."""
        logger.info("=" * 60)
        logger.info("Starting crypto position check and opportunity scan...")

        try:
            scan_report = self.trading_manager.scan_and_execute(
                CRYPTO_WATCHLIST, CRYPTO_POSITION_SIZE_USD, CRYPTO_MAX_POSITIONS
            )
            if scan_report.get('executed'):
                logger.info(f"Crypto scanner executed {len(scan_report['executed'])} trade(s):")
                for trade in scan_report['executed']:
                    qty_str = f"{trade['qty']} " if trade.get('qty') else ''
                    logger.info(f"  {trade['side'].upper()} {qty_str}{trade['symbol']} — {trade['reason']}")
            if scan_report.get('errors'):
                logger.warning(f"Crypto scanner errors: {scan_report['errors']}")

            # Apply stop-loss/trailing-stop/re-entry logic to crypto positions only —
            # this job isn't market-hours gated, but stock positions already get
            # checked by the market-hours job and must not be touched here (see
            # check_positions() docstring for why an unscoped call is unsafe).
            report = self.trading_manager.check_positions(asset_class='crypto')
            report['scan_report'] = scan_report

            logger.info(f"Crypto check completed at {report.get('timestamp')}")
            if report.get('actions_taken'):
                logger.info(f"Actions: {len(report['actions_taken'])} recommendation(s)")
                for action in report['actions_taken']:
                    logger.info(f"  - {action.get('action')}: {action.get('symbol')} - {action.get('recommendation')}")
            if report.get('errors'):
                logger.warning(f"Errors: {report['errors']}")

        except Exception as e:
            logger.error(f"Error in crypto position check job: {e}")

    def start(self):
        """Start the market hours scheduler"""
        if self.is_running:
            logger.warning("Scheduler is already running")
            return
        
        try:
            # Schedule the check job to run every CHECK_INTERVAL_MINUTES; market-hours
            # filtering happens inside the job itself via _is_market_open()
            trigger = IntervalTrigger(minutes=CHECK_INTERVAL_MINUTES, timezone=TIMEZONE)
            self.scheduler.add_job(
                self._check_positions_job,
                trigger=trigger,
                id='position_check',
                name='Market Hours Position Check',
                replace_existing=True
            )

            crypto_trigger = IntervalTrigger(minutes=CRYPTO_CHECK_INTERVAL_MINUTES, timezone=TIMEZONE)
            self.scheduler.add_job(
                self._check_crypto_job,
                trigger=crypto_trigger,
                id='crypto_position_check',
                name='24/7 Crypto Position Check',
                replace_existing=True
            )

            self.scheduler.start()
            self.is_running = True
            logger.info("=" * 60)
            logger.info(f"Market Hours Scheduler started")
            logger.info(f"Market hours: {MARKET_OPEN_HOUR}:{MARKET_OPEN_MINUTE:02d} - {MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MINUTE:02d} ET")
            logger.info(f"Check interval: Every {CHECK_INTERVAL_MINUTES} minutes (Mon-Fri only)")
            logger.info(f"Crypto watchlist: {CRYPTO_WATCHLIST} — checked every {CRYPTO_CHECK_INTERVAL_MINUTES} minutes, 24/7")
            logger.info("=" * 60)
        
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
            raise
    
    def stop(self):
        """Stop the scheduler"""
        if not self.is_running:
            logger.warning("Scheduler is not running")
            return
        
        try:
            self.scheduler.shutdown()
            self.is_running = False
            logger.info("Scheduler stopped")
        except Exception as e:
            logger.error(f"Error stopping scheduler: {e}")
    
    def get_status(self) -> Dict:
        """Get scheduler status"""
        return {
            'running': self.is_running,
            'last_check': self.last_check,
            'check_history_size': len(self.check_history),
            'jobs': len(self.scheduler.get_jobs())
        }
    
    def get_history(self, limit: int = 10) -> list:
        """Get last N position checks"""
        return self.check_history[-limit:]


# Global scheduler instance
_scheduler = None


def get_scheduler() -> MarketHoursScheduler:
    """Get or create the global scheduler instance"""
    global _scheduler
    if _scheduler is None:
        _scheduler = MarketHoursScheduler()
    return _scheduler
