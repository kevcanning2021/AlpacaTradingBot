import smtplib
from email.message import EmailMessage
from typing import Tuple
from config import settings


class EmailNotifier:
    """Simple SMTP email notifier for strategy change alerts."""

    def __init__(self):
        self.enabled = settings.EMAIL_ENABLED
        self.host = settings.EMAIL_SMTP_HOST
        self.port = settings.EMAIL_SMTP_PORT
        self.username = settings.EMAIL_SMTP_USER
        self.password = settings.EMAIL_SMTP_PASSWORD
        self.sender = settings.EMAIL_FROM
        self.recipients = settings.EMAIL_TO

    def send(self, subject: str, body: str) -> bool:
        """Send an email if email notifications are enabled."""
        if not self.enabled:
            return False
        if not self.host or not self.sender or not self.recipients:
            raise ValueError('Email notifier is enabled but SMTP configuration is incomplete.')

        message = EmailMessage()
        message['Subject'] = subject
        message['From'] = self.sender
        message['To'] = ', '.join(self.recipients)
        message.set_content(body)

        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)

        return True

    def build_strategy_change_email(self, adjustments: dict) -> Tuple[str, str]:
        """Build a subject and body for a strategy change notification."""
        subject = f"Strategy Change Notification - {adjustments.get('timestamp')}"
        changes = '\n'.join(f"- {change}" for change in adjustments.get('changes_made', []))
        rationale = '\n'.join(f"- {reason}" for reason in adjustments.get('rationale', []))

        body = [
            f"Strategy changes were applied at {adjustments.get('timestamp')}",
            '',
            'Previous thresholds:',
            f"- Stop Loss: {adjustments.get('previous_stop_loss')}",
            f"- Re-entry: {adjustments.get('previous_reentry')}",
            '',
            'Changes made:',
            changes or 'None',
            '',
            'Rationale:',
            rationale or 'None',
            '',
            'Review the strategy and stop loss settings in your bot configuration.'
        ]

        return subject, '\n'.join(body)

    def build_daily_report_email(self, report: dict) -> Tuple[str, str]:
        """Build a subject and body for the daily account status report."""
        status = report.get('status', {})
        performance = report.get('performance', {})
        timestamp = report.get('timestamp')

        subject = f"Daily Status Report - {timestamp}"

        lines = [
            f"Daily account summary as of {timestamp}",
            '',
            'Account Status:',
            f"- Status: {status.get('account_status')}",
            f"- Equity: ${status.get('equity', 'N/A')}",
            f"- Buying Power: ${status.get('buying_power', 'N/A')}",
            f"- Open Positions: {status.get('open_positions', 0)}",
            ''
        ]

        positions = status.get('positions') or []
        if positions:
            lines.append('Positions:')
            for pos in positions:
                symbol = pos.get('symbol')
                qty = pos.get('qty')
                current = float(pos.get('current_price', 0))
                entry = float(pos.get('avg_entry_price', 0))
                pnl = float(pos.get('unrealized_pl', 0))
                pnl_pct = float(pos.get('unrealized_plpc', 0)) * 100
                lines.append(f"- {symbol}: {qty} shares @ ${current:.2f} (Entry: ${entry:.2f}, P&L: ${pnl:.2f} / {pnl_pct:.2f}%)")
            lines.append('')

        lines.extend([
            'Performance:',
            f"- Total unrealized P&L: ${performance.get('total_pnl', 0):.2f}",
            f"- Winning positions: {performance.get('winning_positions', 0)}",
            f"- Losing positions: {performance.get('losing_positions', 0)}",
            f"- Win streak: {performance.get('win_streak', 0)}",
            f"- Loss streak: {performance.get('loss_streak', 0)}",
            f"- Account return: {performance.get('account_return', 0):.2f}%"
        ])

        return subject, '\n'.join(lines)
