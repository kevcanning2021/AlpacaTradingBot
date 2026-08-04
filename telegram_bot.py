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
from strategy_check import _backtest_watchlist, _simulate_trades

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}'
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_history.json')
POLL_TIMEOUT_SECONDS = 30  # Telegram long-polls: holds the request open until a message arrives or this elapses
OPTIMIZE_LOOKBACK_BARS = 600  # split in half: older = training, newer = untouched holdout
OPTIMIZE_BUY_GRID = [55, 60, 65, 70]
OPTIMIZE_SELL_GRID = [70, 75, 80, 85, 90]
OPTIMIZE_MIN_TRAIN_TRADES = 5  # grid cells below this are too thin to trust, skipped

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
    (BUY_RSI_MAX/SELL_RSI_MIN, current live thresholds, continuous-EMA methodology
    -- see strategy_check.py's module docstring), on demand instead of waiting for
    the daily cron. Read-only: this is analysis only, never touches live thresholds
    or places any order. Not a systematic/multi-candidate search -- see STRATEGY.md
    "Rejected hypotheses" for why that kind of test needs careful out-of-sample
    review, not a one-tap command."""
    lines = [f"BUY_RSI_MAX={OpportunityScanner.BUY_RSI_MAX}, SELL_RSI_MIN={OpportunityScanner.SELL_RSI_MIN}", '']
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
    """Grid-searches BUY_RSI_MAX x SELL_RSI_MIN with a genuine out-of-sample split
    baked in -- fetches OPTIMIZE_LOOKBACK_BARS (600) bars per symbol, trains the
    grid search on the OLDER half only, then evaluates both the current live
    thresholds and whatever looked best in training against the NEWER half, which
    the search never saw. This is exactly the check that caught the 2026-07-23
    systematic-screen overfitting mistake (see STRATEGY.md "Rejected hypotheses") --
    baked into the tool itself instead of relying on someone remembering to build
    it by hand each time. Read-only: reports only, never changes a live threshold."""
    lines = []
    for label, watchlist in (('Stock', settings.WATCHLIST), ('Crypto', settings.CRYPTO_WATCHLIST)):
        if not watchlist:
            continue
        bars_by_symbol = client.get_bars_multi(watchlist, limit=OPTIMIZE_LOOKBACK_BARS)
        older = {s: b[:len(b) // 2] for s, b in bars_by_symbol.items()}
        newer = {s: b[len(b) // 2:] for s, b in bars_by_symbol.items()}

        best = None
        for buy in OPTIMIZE_BUY_GRID:
            for sell in OPTIMIZE_SELL_GRID:
                trades = [t for bars in older.values() for t in _simulate_trades(bars, buy, sell)]
                if len(trades) < OPTIMIZE_MIN_TRAIN_TRADES:
                    continue
                exp = (sum(trades) / len(trades)) * 100
                if best is None or exp > best['train_exp']:
                    best = {'buy': buy, 'sell': sell, 'train_trades': len(trades), 'train_exp': exp}

        def holdout_expectancy(buy, sell):
            trades = [t for bars in newer.values() for t in _simulate_trades(bars, buy, sell)]
            return (sum(trades) / len(trades) * 100, len(trades)) if trades else (None, 0)

        live_buy, live_sell = OpportunityScanner.BUY_RSI_MAX, OpportunityScanner.SELL_RSI_MIN
        live_exp, live_n = holdout_expectancy(live_buy, live_sell)
        lines.append(f"{label} (holdout = newer half, never used for the search below):")
        lines.append(
            f"  Current live {live_buy}/{live_sell}: {live_exp:+.3f}%/trade ({live_n} trades)"
            if live_exp is not None else f"  Current live {live_buy}/{live_sell}: no holdout trades"
        )

        if best is None:
            lines.append("  No training-window combo had enough trades to trust.")
            continue
        best_exp, best_n = holdout_expectancy(best['buy'], best['sell'])
        if best['buy'] == live_buy and best['sell'] == live_sell:
            lines.append("  (Best in training was the same as current live -- nothing new to compare.)")
        elif best_exp is None or best_n < OPTIMIZE_MIN_TRAIN_TRADES:
            # Too few holdout trades to trust a "held up" claim -- the exact same
            # false-confidence risk this whole split exists to catch, just one
            # step later (thin holdout instead of thin training). Say so plainly
            # rather than reporting a number that looks meaningful but isn't.
            lines.append(
                f"  Best in training {best['buy']}/{best['sell']} ({best['train_exp']:+.3f}%/trade, "
                f"{best['train_trades']} trades): only {best_n} holdout trade(s) -- too thin to trust either way"
            )
        elif live_exp is not None and best_exp <= live_exp:
            lines.append(
                f"  Best in training {best['buy']}/{best['sell']} ({best['train_exp']:+.3f}%/trade in training) "
                f"-> {best_exp:+.3f}%/trade on holdout ({best_n} trades) -- NOT better out-of-sample, keep current"
            )
        else:
            lines.append(
                f"  Best in training {best['buy']}/{best['sell']} ({best['train_exp']:+.3f}%/trade in training) "
                f"-> {best_exp:+.3f}%/trade on holdout ({best_n} trades) -- held up out-of-sample, worth a closer look"
            )

    lines.append('')
    lines.append('Analysis only -- no thresholds changed.')
    return '\n'.join(lines)


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
