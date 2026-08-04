"""Always-on Telegram command bot: read-only account info on demand, from your
phone, without SSH. Separate from telegram_notifier.py's one-way alert pushes --
this listens for incoming commands and replies.

Runs as its own systemd service (long-polls Telegram continuously, unlike
watchdog.py/strategy_check.py which are cron one-shots) since Telegram's
getUpdates has no cron-friendly "check once and exit" mode that doesn't risk
missing messages between runs.

Read-only by design: every command only calls AlpacaClient getters (get_account,
get_positions) or reads trade_history.json -- no create_order/close_position path
exists anywhere in this file. Only responds to TELEGRAM_CHAT_ID; messages from
anyone else (if the bot's username were ever discovered) are silently ignored.
"""
import json
import logging
import os
import time
import urllib.request
import urllib.error

from alpaca_client import AlpacaClient
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}'
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_history.json')
POLL_TIMEOUT_SECONDS = 30  # Telegram long-polls: holds the request open until a message arrives or this elapses

HELP_TEXT = (
    "AlpacaTradingBot commands (read-only, test account):\n"
    "/status - equity, cash, buying power, open positions\n"
    "/positions - detailed open position list\n"
    "/history - last 10 closed trades\n"
    "/help - this message"
)


def send_message(chat_id: str, text: str):
    url = f'{API_BASE}/sendMessage'
    payload = json.dumps({'chat_id': chat_id, 'text': text}).encode()
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                logger.error(f'Telegram sendMessage returned HTTP {resp.status}')
    except urllib.error.HTTPError as e:
        logger.error(f'Telegram sendMessage failed: HTTP {e.code}: {e.read().decode(errors="replace")}')
    except Exception as e:
        logger.error(f'Telegram sendMessage failed: {e}')


def get_updates(offset):
    url = f'{API_BASE}/getUpdates?timeout={POLL_TIMEOUT_SECONDS}'
    if offset is not None:
        url += f'&offset={offset}'
    req = urllib.request.Request(url)
    # timeout slightly longer than the long-poll window itself, so the read
    # doesn't get cut off right as Telegram is about to respond
    with urllib.request.urlopen(req, timeout=POLL_TIMEOUT_SECONDS + 10) as resp:
        data = json.loads(resp.read().decode())
    return data.get('result', [])


def format_status(client: AlpacaClient) -> str:
    account = client.get_account()
    positions = client.get_positions()
    lines = [
        f"Equity: ${account.get('equity')}",
        f"Cash: ${account.get('cash')}",
        f"Buying power: ${account.get('buying_power')}",
        f"Open positions: {len(positions)}",
    ]
    for p in positions:
        lines.append(f"  {p['symbol']}: {float(p['unrealized_plpc'])*100:+.2f}%")
    return '\n'.join(lines)


def format_positions(client: AlpacaClient) -> str:
    positions = client.get_positions()
    if not positions:
        return 'No open positions.'
    lines = []
    for p in positions:
        entry = float(p.get('avg_entry_price', 0))
        current = float(p.get('current_price', 0))
        pnl = float(p.get('unrealized_pl', 0))
        pnl_pct = float(p.get('unrealized_plpc', 0)) * 100
        lines.append(
            f"{p['symbol']}: {p.get('qty')} sh\n"
            f"  Entry ${entry:.2f} -> Current ${current:.2f}\n"
            f"  P&L ${pnl:.2f} ({pnl_pct:+.2f}%)"
        )
    return '\n\n'.join(lines)


def format_history() -> str:
    if not os.path.exists(TRADE_HISTORY_FILE):
        return 'No closed trades yet.'
    try:
        with open(TRADE_HISTORY_FILE) as f:
            history = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return f'Could not read trade history: {e}'
    if not history:
        return 'No closed trades yet.'
    lines = []
    for t in history[-10:]:
        pnl_pct = t.get('pnl_pct')
        pct_str = f"{pnl_pct*100:+.2f}%" if pnl_pct is not None else 'n/a'
        lines.append(f"{t.get('timestamp', '')[:10]} {t.get('symbol')}: {pct_str}")
    return '\n'.join(reversed(lines))


def handle_command(text: str, chat_id: str, client: AlpacaClient):
    command = text.strip().split()[0].lower() if text.strip() else ''
    try:
        if command == '/status':
            send_message(chat_id, format_status(client))
        elif command == '/positions':
            send_message(chat_id, format_positions(client))
        elif command == '/history':
            send_message(chat_id, format_history())
        elif command == '/help' or command == '/start':
            send_message(chat_id, HELP_TEXT)
        else:
            send_message(chat_id, f"Unknown command '{text}'. Send /help for the list.")
    except Exception as e:
        logger.error(f'Error handling command {text!r}: {e}')
        send_message(chat_id, f'Error: {e}')


def main():
    if not settings.TELEGRAM_ENABLED:
        logger.error('TELEGRAM_ENABLED is false -- telegram_bot.py has nothing to do. Exiting.')
        return
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.error('TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing. Exiting.')
        return

    client = AlpacaClient()
    offset = None
    logger.info('Telegram command bot started, long-polling for messages...')

    while True:
        try:
            updates = get_updates(offset)
        except Exception as e:
            logger.error(f'get_updates failed, retrying in 10s: {e}')
            time.sleep(10)
            continue

        for update in updates:
            offset = update['update_id'] + 1
            message = update.get('message')
            if not message or 'text' not in message:
                continue
            chat_id = str(message.get('chat', {}).get('id', ''))
            if chat_id != settings.TELEGRAM_CHAT_ID:
                logger.warning(f'Ignoring message from unauthorized chat_id={chat_id}')
                continue
            logger.info(f'Command received: {message["text"]!r}')
            handle_command(message['text'], chat_id, client)


if __name__ == '__main__':
    main()
