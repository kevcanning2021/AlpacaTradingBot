"""Pings Telegram exactly once when the market transitions open<->closed.

Deliberately state-driven, not a hardcoded schedule -- NYSE open/close times
shift with DST and holidays (half-days, full closures), and the official
/clock endpoint already accounts for all of that. Run this frequently via
cron (every 5 min) during the hours the market could plausibly be open or
about to open; it's a no-op every run except the one where is_open actually
flips, detected via a tiny state file (STATE_PATH) so a restart or a missed
run can't cause a duplicate or a skipped notification -- the next run just
compares against the last known state again.

Fleet-wide, not bot-specific: lives on Main only (it already has Telegram
credentials) since NYSE market hours apply to Main and Sofi alike, and Nova
has no Telegram of its own to notify through anyway.
"""
import json
import logging
import os

from alpaca_client import AlpacaClient
from telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.path.dirname(__file__), 'market_hours_state.json')


def _load_last_state() -> bool | None:
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH) as f:
            return json.load(f).get('is_open')
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load market hours state, treating as unknown: %s", e)
        return None


def _save_state(is_open: bool) -> None:
    with open(STATE_PATH, 'w') as f:
        json.dump({'is_open': is_open}, f)


def main() -> None:
    client = AlpacaClient()
    clock = client.get_clock()
    is_open = clock['is_open']

    last_state = _load_last_state()
    _save_state(is_open)

    if last_state is None or last_state == is_open:
        return  # first run ever, or no transition -- nothing to announce

    notifier = TelegramNotifier()
    if is_open:
        notifier.send("Market is open", f"NYSE regular session started. Closes at {clock['next_close']}.")
    else:
        notifier.send("Market is closed", f"NYSE regular session ended. Next open: {clock['next_open']}.")


if __name__ == '__main__':
    main()
