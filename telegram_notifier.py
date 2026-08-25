import json
import logging
import urllib.request
import urllib.error
from typing import Tuple
from config import settings

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram notifier via the Telegram Bot API -- drop-in replacement for
    WhatsAppNotifier (same send()/build_*_email() interface), switched to 2026-08-04
    after CallMeBot's free WhatsApp quota ran out. Free, no message limits."""

    def __init__(self):
        self.enabled = settings.TELEGRAM_ENABLED
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID

    def send(self, subject: str, body: str) -> bool:
        if not self.enabled:
            return False
        if not self.bot_token or not self.chat_id:
            raise ValueError('Telegram notifier is enabled but TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.')

        # Plain text, no parse_mode -- Telegram's Markdown/HTML modes reject the
        # whole message on unescaped special characters (prices, symbols like
        # BTC/USD can contain them), which would silently drop real alerts.
        text = f"{subject}\n\n{body}"
        url = f'https://api.telegram.org/bot{self.bot_token}/sendMessage'
        payload = json.dumps({'chat_id': self.chat_id, 'text': text}).encode()

        req = urllib.request.Request(url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status != 200:
                    raise Exception(f'Telegram returned HTTP {resp.status}')
        except urllib.error.HTTPError as e:
            raise Exception(f'Telegram returned HTTP {e.code}: {e.read().decode(errors="replace")}')

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
