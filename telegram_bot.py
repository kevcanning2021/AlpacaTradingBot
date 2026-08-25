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
from scanner import OpportunityScanner
from strategy_check import _backtest_watchlist

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}'
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_history.json')
POLL_TIMEOUT_SECONDS = 30  # Telegram long-polls: holds the request open until a message arrives or this elapses

# Single source of truth for both the /help text and Telegram's native command
# menu (set_bot_commands below) -- BOT_COMMANDS is a list of (name, description)
# with no leading slash (Telegram's setMyCommands convention; it adds the slash
# in its own UI). Keeping this one list avoids the menu and /help silently
# drifting apart if a command is ever added or changed.
BOT_COMMANDS = [
    ("status", "equity, cash, buying power, open positions"),
    ("positions", "detailed open position list"),
    ("history", "last 10 closed trades"),
    ("backtest", "re-run today's BUY_RSI_MAX/SELL_RSI_MIN backtest now"),
    ("optimize", "grid-search RSI thresholds with a real train/holdout split"),
    ("help", "this message"),
]
HELP_TEXT = "AlpacaTradingBot commands (read-only, test account):\n" + "\n".join(
    f"/{name} - {desc}" for name, desc in BOT_COMMANDS
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


def set_bot_commands():
    """Registers BOT_COMMANDS with Telegram's setMyCommands so they show up in the
    bot's native "/" menu button, not just as typed text. Called once at startup --
    idempotent (re-sending the same list is a harmless no-op), so it also
    self-heals the menu if a command was ever added without this having run yet.

    Explicitly scoped to "all_private_chats" (this bot only ever runs in a private
    chat), not left at Telegram's "default" scope -- scope precedence is
    chat-specific > all_private_chats > default, so a stale all_private_chats
    override (found 2026-08-04: a leftover single "hi" command from manual
    BotFather testing before this file existed) silently wins over a default-scope
    registration and hides it. Setting commands directly at all_private_chats
    overwrites that stale entry instead of being shadowed by it."""
    url = f'{API_BASE}/setMyCommands'
    payload = json.dumps({
        'commands': [{'command': name, 'description': desc} for name, desc in BOT_COMMANDS],
        'scope': {'type': 'all_private_chats'},
    }).encode()
    req = urllib.request.Request(url, data=payload, method='POST')
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                logger.error(f'setMyCommands returned HTTP {resp.status}')
            else:
                logger.info(f'Registered {len(BOT_COMMANDS)} commands with Telegram\'s menu (scope=all_private_chats)')
    except Exception as e:
        logger.error(f'setMyCommands failed (menu may be stale, bot still works via typed commands): {e}')


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


def run_backtest(client: AlpacaClient) -> str:
    """Re-runs the exact same backtest strategy_check.py already does once/day
    (current live thresholds/strategy per watchlist, read from scanner.py so
    this never drifts -- see strategy_check.py's module docstring), on demand
    instead of waiting for the daily cron. Read-only: this is analysis only,
    never touches live thresholds or places any order. Not a systematic/
    multi-candidate search -- see STRATEGY.md "Rejected hypotheses" for why
    that kind of test needs careful out-of-sample review, not a one-tap
    command."""
    lines = [
        f"Stock: Bollinger({OpportunityScanner.BOLLINGER_PERIOD},{OpportunityScanner.BOLLINGER_STD}) "
        f"oversold RSI<{OpportunityScanner.BOLLINGER_OVERSOLD_RSI} + EMA9/21 crossover "
        f"BUY_RSI_MAX={OpportunityScanner.BUY_RSI_MAX} (dual, either fires), SELL_RSI_MIN={OpportunityScanner.SELL_RSI_MIN}",
        f"Crypto: Donchian breakout({OpportunityScanner.DONCHIAN_BREAKOUT_PERIOD}/"
        f"{OpportunityScanner.DONCHIAN_EXIT_PERIOD})",
        '',
    ]
    for label, watchlist in (('Stock', settings.WATCHLIST), ('Crypto', settings.CRYPTO_WATCHLIST)):
        if not watchlist:
            continue
        result = _backtest_watchlist(client, watchlist)
        if result is None:
            lines.append(f"{label}: no trades in this window")
            continue
        lines.append(
            f"{label}: {result['trade_count']} trades, "
            f"{result['expectancy_pct']:+.3f}%/trade, {result['win_rate_pct']:.1f}% win"
        )
    return '\n'.join(lines)


def run_optimize(client: AlpacaClient) -> str:
    """Formerly grid-searched BUY_RSI_MAX x SELL_RSI_MIN with a genuine
    out-of-sample split (older half trains, newer untouched half evaluates --
    the check that caught the 2026-07-23 systematic-screen overfitting
    mistake, see STRATEGY.md "Rejected hypotheses"). Retired 2026-08-24: ever
    since stocks went dual (2026-08-17, Bollinger + EMA9/21, either can open
    a position) and crypto switched to Donchian breakout (period-based, no
    RSI entry parameter at all), neither live strategy is a single RSI
    threshold pair anymore, so a BUY_RSI_MAX/SELL_RSI_MIN grid doesn't map
    onto anything actually deployed on either watchlist. A real optimizer for
    either current strategy would need its own dedicated grid (Bollinger's
    period/std/oversold-RSI for stocks, Donchian's breakout/exit period for
    crypto), not a reuse of this one -- not built since neither has asked for
    it yet. Use /backtest for current live numbers on both watchlists
    instead. Read-only either way: this always was analysis only, never
    changed a live threshold."""
    return ('/optimize is retired -- it grid-searched BUY_RSI_MAX/SELL_RSI_MIN, which neither '
            'live strategy uses anymore (stock: dual Bollinger+EMA9/21 since 2026-08-17; '
            'crypto: Donchian breakout since 2026-08-24, no RSI entry parameter). '
            'Use /backtest for current live numbers on both watchlists.')


def handle_command(text: str, chat_id: str, client: AlpacaClient):
    command = text.strip().split()[0].lower() if text.strip() else ''
    try:
        if command == '/status':
            send_message(chat_id, format_status(client))
        elif command == '/positions':
            send_message(chat_id, format_positions(client))
        elif command == '/history':
            send_message(chat_id, format_history())
        elif command == '/backtest':
            send_message(chat_id, 'Running backtest against fresh data, one moment...')
            send_message(chat_id, run_backtest(client))
        elif command == '/optimize':
            send_message(chat_id, 'Grid-searching thresholds with an out-of-sample check, this takes a bit longer...')
            send_message(chat_id, run_optimize(client))
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

    set_bot_commands()
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
