import urllib.request
import urllib.parse
import logging
from typing import Tuple
from config import settings

logger = logging.getLogger(__name__)


class WhatsAppNotifier:
    """WhatsApp notifier via CallMeBot API (drop-in replacement for EmailNotifier)."""

    def __init__(self):
        self.enabled = settings.WHATSAPP_ENABLED
        self.phone = settings.WHATSAPP_PHONE
        self.apikey = settings.WHATSAPP_APIKEY

    def send(self, subject: str, body: str) -> bool:
        if not self.enabled:
            return False
        if not self.phone or not self.apikey:
            raise ValueError('WhatsApp notifier is enabled but WHATSAPP_PHONE or WHATSAPP_APIKEY is missing.')

        text = f"*{subject}*\n{body}"
        encoded = urllib.parse.quote(text)
        url = f'https://api.callmebot.com/whatsapp.php?phone={self.phone}&text={encoded}&apikey={self.apikey}'

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            if status != 200:
                raise Exception(f'CallMeBot returned HTTP {status}')

        return True

    def build_strategy_change_email(self, adjustments: dict) -> Tuple[str, str]:
        subject = f"Strategy Change - {adjustments.get('timestamp')}"
        changes = '\n'.join(f"- {c}" for c in adjustments.get('changes_made', []))
        rationale = '\n'.join(f"- {r}" for r in adjustments.get('rationale', []))
        body = '\n'.join([
            f"Applied at {adjustments.get('timestamp')}",
            '',
            f"Stop Loss: {adjustments.get('previous_stop_loss')}",
            f"Re-entry: {adjustments.get('previous_reentry')}",
            '',
            'Changes:',
            changes or 'None',
            '',
            'Rationale:',
            rationale or 'None',
        ])
        return subject, body

    def build_position_alert_email(self, report: dict) -> Tuple[str, str]:
        actions = report.get('actions_taken', [])
        subject = f"Position Alert: {len(actions)} action(s)"
        lines = [f"Checked at {report.get('timestamp')}", '']
        for a in actions:
            lines.append(f"{a.get('action')}: {a.get('symbol')} ({a.get('pnl_pct', a.get('pullback_pct'))}%)")
            lines.append(f"  {a.get('recommendation')}")
        return subject, '\n'.join(lines)

    def build_trade_execution_email(self, scan_report: dict) -> Tuple[str, str]:
        executed = scan_report.get('executed', [])
        subject = f"Trade Alert: {len(executed)} order(s)"
        lines = [f"Executed at {scan_report.get('timestamp')}", '']

        buys = [e for e in executed if e['side'] == 'buy']
        sells = [e for e in executed if e['side'] == 'sell']

        if buys:
            lines.append('Buys:')
            for t in buys:
                lines.append(f"  BUY {t['qty']} {t['symbol']} @ ~${t['price']:.2f}")
                lines.append(f"  Reason: {t['reason']}")
        if sells:
            lines.append('Sells:')
            for t in sells:
                lines.append(f"  SELL {t['symbol']} — {t['reason']}")
        if scan_report.get('errors'):
            lines.append('Errors: ' + ', '.join(scan_report['errors']))

        return subject, '\n'.join(lines)
