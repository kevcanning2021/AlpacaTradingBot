"""Standalone VPS watchdog for the test account: checks service health, log errors,
and account state; sends a WhatsApp alert only when something's actually wrong.

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
from whatsapp_notifier import WhatsAppNotifier
from config import settings

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchdog_state.json')
SERVICES = ['alpaca-bot.service', 'alpaca-bot-test.service', 'alpaca-dashboard.service']
ALERT_COOLDOWN_SECONDS = 2 * 60 * 60

# Orders already investigated and confirmed not to be bugs — don't re-flag them.
KNOWN_MANUAL_ORDER_IDS = {
    'e9b313b1-7668-45f2-8544-b7bb0cc83cd2',  # 2026-07-10 rebalance, placed manually from dev machine
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
    issues = []
    for svc in SERVICES:
        result = subprocess.run(['systemctl', 'is-active', svc], capture_output=True, text=True)
        status = result.stdout.strip()
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
    issues = []
    new_order_ids = set(seen_order_ids)
    client = AlpacaClient()

    for p in client.get_positions():
        pnl_pct = float(p['unrealized_plpc'])
        symbol = p['symbol']
        if pnl_pct <= -settings.STOP_LOSS_THRESHOLD:
            issues.append((f'stop_loss_breach:{symbol}', f'{symbol} is down {pnl_pct * 100:.1f}% — past the {settings.STOP_LOSS_THRESHOLD * 100:.0f}% stop-loss threshold'))

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
        if source != 'access_key':
            issues.append((f'unattributed_order:{oid}', f'Order {oid} ({side} {symbol}) has source="{source}", not the bot\'s own key — check if trader.py\'s win/loss-streak tracking missed a real trade'))
        elif side == 'sell':
            issues.append((f'bot_sell:{oid}', f'Bot placed a SELL on {symbol} (order {oid}) — likely a stop-loss/trailing-stop/scanner exit firing'))

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
        WhatsAppNotifier().send('AlpacaTradingBot Watchdog', '\n\n'.join(messages))

    state['active_alerts'] = active
    state['seen_order_ids'] = list(seen_order_ids)
    state['last_log_check'] = now.isoformat()
    save_state(state)


if __name__ == '__main__':
    main()
