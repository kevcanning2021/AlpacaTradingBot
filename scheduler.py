import logging
from datetime import datetime
from typing import Dict
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import pytz
from trader import TradingManager
from config.settings import (
    CHECK_INTERVAL_MINUTES,
    MARKET_OPEN_HOUR,
    MARKET_OPEN_MINUTE,
    MARKET_CLOSE_HOUR,
    MARKET_CLOSE_MINUTE,
    TIMEZONE,
    REPORT_TIMEZONE,
    DAILY_REPORT_HOUR,
    DAILY_REPORT_MINUTE
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
        logger.info("Starting position check...")
        
        try:
            report = self.trading_manager.check_positions()
            self.last_check = report
            self.check_history.append(report)

            performance = self.trading_manager.analyze_performance()
            report['performance'] = performance

            strategy_report = self.trading_manager.adjust_strategy()
            report['strategy_adjustment'] = strategy_report
            
            # Keep only last 100 checks
            if len(self.check_history) > 100:
                self.check_history.pop(0)
            
            logger.info(f"Position check completed at {report.get('timestamp')}")
            if report.get('actions_taken'):
                logger.info(f"Actions: {len(report['actions_taken'])} recommendation(s)")
                for action in report['actions_taken']:
                    logger.info(f"  - {action.get('action')}: {action.get('symbol')} - {action.get('recommendation')}")
            
            if strategy_report.get('changes_made'):
                logger.info(f"Strategy adjustments applied: {strategy_report.get('changes_made')}")
            
            if report.get('errors'):
                logger.warning(f"Errors: {report['errors']}")
        
        except Exception as e:
            logger.error(f"Error in position check job: {e}")

    def _send_daily_report_job(self):
        """Job that emails a daily account status report."""
        logger.info("=" * 60)
        logger.info("Sending daily status report...")

        try:
            report = self.trading_manager.build_daily_report()
            if self.trading_manager.notifier.enabled:
                subject, body = self.trading_manager.notifier.build_daily_report_email(report)
                self.trading_manager.notifier.send(subject, body)
                logger.info("Daily status report email sent successfully")
            else:
                logger.info("Email notifications disabled; skipping daily report email")
        except Exception as e:
            logger.error(f"Error sending daily report: {e}")

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

            daily_trigger = CronTrigger(
                hour=DAILY_REPORT_HOUR,
                minute=DAILY_REPORT_MINUTE,
                day_of_week='mon-fri',
                timezone=REPORT_TIMEZONE
            )
            self.scheduler.add_job(
                self._send_daily_report_job,
                trigger=daily_trigger,
                id='daily_report',
                name='Daily Status Report Email',
                replace_existing=True
            )

            self.scheduler.start()
            self.is_running = True
            logger.info("=" * 60)
            logger.info(f"Market Hours Scheduler started")
            logger.info(f"Market hours: {MARKET_OPEN_HOUR}:{MARKET_OPEN_MINUTE:02d} - {MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MINUTE:02d} ET")
            logger.info(f"Check interval: Every {CHECK_INTERVAL_MINUTES} minutes (Mon-Fri only)")
            logger.info(f"Daily report: {DAILY_REPORT_HOUR:02d}:{DAILY_REPORT_MINUTE:02d} {REPORT_TIMEZONE} (Mon-Fri)")
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
