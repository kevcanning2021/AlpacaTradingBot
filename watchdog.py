"""Standalone VPS watchdog for the test account: checks service health, log errors,
and account state; sends a Telegram alert only when something's actually wrong.

Meant to run via crontab every 15 min on the VPS itself, independent of any
Claude Code session — see KNOWN_LIMITATIONS.md / project notes for why this
exists (a session-only health check dies when the session/laptop does).

State is kept in a small JSON file so:
- the same issue doesn't re-alert every 15 min (only on first detection, then
  again every ALERT_COOLDOWN_SECONDS if it's still unresolved)
- log-error scanning only looks at genuinely new journal lines since last run
- already-known/explained orders (see KNOWN_MANUAL_ORDER_IDS) don't re-fire
"""
import json
import os
import subprocess
from datetime import datetime, timezone

from alpaca_client import AlpacaClient
from telegram_notifier import TelegramNotifier
from config import settings

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchdog_state.json')
SERVICES = ['alpaca-bot.service', 'alpaca-bot-test.service', 'alpaca-dashboard.service', 'alpaca-telegram-bot.service']
ALERT_COOLDOWN_SECONDS = 2 * 60 * 60

# Orders already investigated and confirmed not to be bugs — don't re-flag them.
KNOWN_MANUAL_ORDER_IDS = {
    'e9b313b1-7668-45f2-8544-b7bb0cc83cd2',  # 2026-07-10 rebalance, placed manually from dev machine
    '47a7e888-f544-4533-a471-8c1bdb05b7b4',  # 2026-07-13 accidental AAPL close_position() call
                                              # while unit-testing threshold logic from dev machine
                                              # against the real client instead of a stub — see
                                              # project memory Lesson #7. +1.7% gain, no harm; not a
                                              # bot trade, so trader.py's streak tracking correctly
                                              # never saw it.
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'active_alerts': {}, 'seen_order_ids': [], 'last_log_check': None}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def check_services():
    # systemctl is-active accepts multiple units and prints one status line per unit,
    # in the given order, regardless of exit code -- one subprocess spawn instead of
    # one per service (confirmed live: a mix of active/inactive units still lists all
    # statuses in order).
    issues = []
    result = subprocess.run(['systemctl', 'is-active'] + SERVICES, capture_output=True, text=True)
    statuses = result.stdout.splitlines()
    for svc, status in zip(SERVICES, statuses):
        if status != 'active':
            issues.append((f'service_down:{svc}', f'{svc} is "{status}", not active'))
    return issues


def check_new_log_errors(since_iso):
    issues = []
    cmd = ['journalctl', '-u', 'alpaca-bot-test.service', '--no-pager', '-o', 'cat']
    if since_iso:
        cmd += ['--since', since_iso]
    else:
        cmd += ['-n', '50']
    result = subprocess.run(cmd, capture_output=True, text=True)
    bad_lines = [l for l in result.stdout.splitlines() if 'Traceback' in l or 'ERROR' in l]
    if bad_lines:
        sample = '\n'.join(bad_lines[-5:])
        issues.append(('log_errors', f'New errors in alpaca-bot-test.service log:\n{sample}'))
    return issues


def check_account(seen_order_ids):
    """Checks positions and orders independently -- a failure fetching one (e.g. a
    transient Alpaca API timeout) must not skip the other, and must not crash the
    whole script before check_services()/check_new_log_errors() even run. Each
    failure becomes its own cooldown-managed issue instead of an unhandled
    exception, so a rough API patch is reported once (like any other issue here),
    not silently swallowed by the process dying."""
    issues = []
    new_order_ids = set(seen_order_ids)
    client = AlpacaClient()

    try:
        for p in client.get_positions():
            pnl_pct = float(p['unrealized_plpc'])
            symbol = p['symbol']
            if pnl_pct <= -settings.STOP_LOSS_THRESHOLD:
                issues.append((f'stop_loss_breach:{symbol}', f'{symbol} is down {pnl_pct * 100:.1f}% — past the {settings.STOP_LOSS_THRESHOLD * 100:.0f}% stop-loss threshold'))
    except Exception as e:
        issues.append(('api_error:positions', f'Failed to fetch positions from Alpaca: {e}'))

    try:
        for o in client.get_orders():
            oid = o.get('id')
            if not oid or oid in seen_order_ids:
                continue
            new_order_ids.add(oid)
            if oid in KNOWN_MANUAL_ORDER_IDS:
                continue
            source = o.get('source')
            side = o.get('side')
            symbol = o.get('symbol')
            # close_position() (DELETE /positions/{symbol} -- every stop-loss/trailing-stop/
            # scanner-sell exit) returns source=None on Alpaca's paper API immediately after
            # fill, unlike create_order() (POST /orders -- every buy) which returns
            # 'access_key' right away. TRANSIENT, not permanent: order 6a00a285-... (the
            # 2026-07-17 stop-loss close this was built from) showed source=None when checked
            # minutes after fill, then 'access_key' when re-checked 2026-07-21 -- Alpaca
            # apparently backfills/reconciles this field sometime after the close, on its own
            # schedule. Doesn't change the fix: watchdog runs every 15 min and evaluates each
            # order on first sighting, when the field is still most likely None. Only flag a
            # sell as unattributed if its source is something else entirely (neither None nor
            # our own key).
            if side == 'sell' and source in (None, 'access_key'):
                issues.append((f'bot_sell:{oid}', f'Bot placed a SELL on {symbol} (order {oid}) — likely a stop-loss/trailing-stop/scanner exit firing'))
            elif source != 'access_key':
                issues.append((f'unattributed_order:{oid}', f'Order {oid} ({side} {symbol}) has source="{source}", not the bot\'s own key — check if trader.py\'s win/loss-streak tracking missed a real trade'))
    except Exception as e:
        issues.append(('api_error:orders', f'Failed to fetch orders from Alpaca: {e}'))

    return issues, new_order_ids


def main():
    state = load_state()
    active = state.get('active_alerts', {})
    seen_order_ids = set(state.get('seen_order_ids', []))
    now = datetime.now(timezone.utc)

    account_issues, seen_order_ids = check_account(seen_order_ids)
    all_issues = check_services() + check_new_log_errors(state.get('last_log_check')) + account_issues
    current_keys = {key for key, _ in all_issues}

    messages = []
    for key, msg in all_issues:
        last_sent = active.get(key)
        if last_sent is None:
            messages.append(msg)
            active[key] = now.isoformat()
        elif (now - datetime.fromisoformat(last_sent)).total_seconds() > ALERT_COOLDOWN_SECONDS:
            messages.append(f'[STILL ACTIVE] {msg}')
            active[key] = now.isoformat()

    for key in list(active.keys()):
        if key not in current_keys:
            del active[key]

    if messages:
        TelegramNotifier().send('AlpacaTradingBot Watchdog', '\n\n'.join(messages))

    state['active_alerts'] = active
    state['seen_order_ids'] = list(seen_order_ids)
    state['last_log_check'] = now.isoformat()
    save_state(state)


if __name__ == '__main__':
    main()
