import smtplib
from email.message import EmailMessage
from typing import List
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

    def build_strategy_change_email(self, adjustments: dict) -> (str, str):
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
