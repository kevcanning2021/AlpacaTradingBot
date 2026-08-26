"""Standalone VPS watchdog: checks service health, log errors, and account
state across all live Alpaca accounts on this VPS (Production, Test, SOFI,
Trading 2.0); sends a Telegram alert only when something's actually wrong.

Runs from /opt/alpaca-bot-test via crontab every 15 min on the VPS itself,
independent of any Claude Code session -- see KNOWN_LIMITATIONS.md / project
notes for why this exists (a session-only health check dies when the
session/laptop does). Originally Test-account-only; extended 2026-08-25
after a production API-key outage (and, separately, a brief production
401 blip) both went unnoticed until manually checked -- this account's own
.env only ever held this account's own credentials, so the other bots were
invisible to it. Extended again 2026-08-26 to add Trading 2.0 (a separate,
isolated multi-timeframe bot on the same VPS, not part of this repo).
Cross-account read access uses the same ALPACA_PROD_*/ALPACA_SOFI_*/
ALPACA_TRADING2_* env var naming the dashboard already established for the
same purpose (see dashboard/config.py).

State is kept in a small JSON file so:
- the same issue doesn't re-alert every 15 min (only on first detection, then
  again every ALERT_COOLDOWN_SECONDS if it's still unresolved)
- log-error scanning only looks at genuinely new journal lines since last run
  (tracked per service now, not a single global timestamp)
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
SERVICES = [
    'alpaca-bot.service', 'alpaca-bot-test.service', 'alpaca-dashboard.service',
    'alpaca-telegram-bot.service', 'pdt15rev-bot.service', 'trading-2-0.service',
]
ALERT_COOLDOWN_SECONDS = 2 * 60 * 60

# Accounts this watchdog checks positions/orders for, and whose service log
# gets scanned for new tracebacks/ERROR lines. Test's own credentials come
# from this service's own .env (config.settings, same as trader.py uses);
# Production's and SOFI's are separate read-access-only-in-practice
# credentials (Alpaca has no read-only key type, but every call made with
# them here is a GET) added to this service's .env specifically for this
# watchdog.
ACCOUNTS = {
    'production': {
        'label': 'Production',
        'log_unit': 'alpaca-bot.service',
        'api_key': os.getenv('ALPACA_PROD_API_KEY', ''),
        'secret_key': os.getenv('ALPACA_PROD_SECRET_KEY', ''),
    },
    'test': {
        'label': 'Test',
        'log_unit': 'alpaca-bot-test.service',
        'api_key': settings.ALPACA_API_KEY,
        'secret_key': settings.ALPACA_SECRET_KEY,
    },
    'sofi': {
        'label': 'SOFI',
        'log_unit': 'pdt15rev-bot.service',
        'api_key': os.getenv('ALPACA_SOFI_API_KEY', ''),
        'secret_key': os.getenv('ALPACA_SOFI_SECRET_KEY', ''),
    },
    'trading2': {
        'label': 'Trading 2.0',
        'log_unit': 'trading-2-0.service',
        'api_key': os.getenv('ALPACA_TRADING2_API_KEY', ''),
        'secret_key': os.getenv('ALPACA_TRADING2_SECRET_KEY', ''),
    },
}

# Orders already investigated and confirmed not to be bugs — don't re-flag them.
# Order IDs are UUIDs (globally unique regardless of which account placed them),
# so one flat set safely covers all three accounts.
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
            state = json.load(f)
    else:
        state = {}
    state.setdefault('active_alerts', {})
    state.setdefault('seen_order_ids', [])
    # Was a single global ISO string pre-2026-08-25; now per-service-unit so each
    # account's log gets its own "since last run" cursor. A stale string from the
    # old format is discarded rather than misapplied to a new unit -- worst case,
    # the first post-upgrade run does a -n 50 fallback per unit instead of --since,
    # which is harmless (just re-scans a bit of already-seen log).
    last_log_check = state.get('last_log_check')
    if not isinstance(last_log_check, dict):
        last_log_check = {}
    state['last_log_check'] = last_log_check
    return state


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


def check_new_log_errors(unit, since_iso):
    issues = []
    cmd = ['journalctl', '-u', unit, '--no-pager', '-o', 'cat']
    if since_iso:
        cmd += ['--since', since_iso]
    else:
        cmd += ['-n', '50']
    result = subprocess.run(cmd, capture_output=True, text=True)
    bad_lines = [l for l in result.stdout.splitlines() if 'Traceback' in l or 'ERROR' in l]
    if bad_lines:
        sample = '\n'.join(bad_lines[-5:])
        issues.append((f'log_errors:{unit}', f'New errors in {unit} log:\n{sample}'))
    return issues


def check_account(account_key, label, api_key, secret_key, seen_order_ids):
    """Checks positions and orders independently -- a failure fetching one (e.g. a
    transient Alpaca API timeout) must not skip the other, and must not crash the
    whole script before check_services()/check_new_log_errors() even run. Each
    failure becomes its own cooldown-managed issue instead of an unhandled
    exception, so a rough API blip is reported once (like any other issue here),
    not silently swallowed by the process dying."""
    issues = []
    new_order_ids = set(seen_order_ids)

    if not api_key or not secret_key:
        issues.append((f'{account_key}:not_configured', f'[{label}] credentials not set in this watchdog\'s .env — skipping checks for this account'))
        return issues, new_order_ids

    client = AlpacaClient()
    client.api_key = api_key
    client.secret_key = secret_key

    try:
        for p in client.get_positions():
            pnl_pct = float(p['unrealized_plpc'])
            symbol = p['symbol']
            if pnl_pct <= -settings.STOP_LOSS_THRESHOLD:
                issues.append((f'{account_key}:stop_loss_breach:{symbol}', f'[{label}] {symbol} is down {pnl_pct * 100:.1f}% — past the {settings.STOP_LOSS_THRESHOLD * 100:.0f}% stop-loss threshold'))
    except Exception as e:
        issues.append((f'{account_key}:api_error:positions', f'[{label}] Failed to fetch positions from Alpaca: {e}'))

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
            # Alpaca's paper API returns source=None immediately after a fill, on BOTH
            # sides -- not sell-only as first thought. Originally believed this was
            # close_position() (DELETE, every sell) vs. create_order() (POST, every
            # buy) specific, since the sell case (order 6a00a285-...) was the only one
            # checked right after fill; every buy checked before 2026-08-04 just
            # happened to already be days old, past Alpaca's own backfill delay.
            # Disproven the same day: order 70f7f532-... (a genuine scan-buy-SPY-...
            # buy, confirmed legitimate by its client_order_id tag) showed source=None
            # immediately after fill too. TRANSIENT either way -- Alpaca backfills to
            # 'access_key' on its own schedule (days, per the original sell case) --
            # so treat None the same as access_key regardless of side, and only flag
            # an order as unattributed if its source is something else entirely.
            if source in (None, 'access_key'):
                issues.append((f'{account_key}:bot_order:{oid}', f'[{label}] Bot placed a {side.upper()} on {symbol} (order {oid})'))
            else:
                issues.append((f'{account_key}:unattributed_order:{oid}', f'[{label}] Order {oid} ({side} {symbol}) has source="{source}", not this account\'s own key — verify this wasn\'t a manual or unexpected order'))
    except Exception as e:
        issues.append((f'{account_key}:api_error:orders', f'[{label}] Failed to fetch orders from Alpaca: {e}'))

    return issues, new_order_ids


def main():
    state = load_state()
    active = state.get('active_alerts', {})
    seen_order_ids = set(state.get('seen_order_ids', []))
    last_log_check = state['last_log_check']
    now = datetime.now(timezone.utc)

    all_issues = list(check_services())

    for account_key, cfg in ACCOUNTS.items():
        account_issues, seen_order_ids = check_account(
            account_key, cfg['label'], cfg['api_key'], cfg['secret_key'], seen_order_ids
        )
        all_issues += account_issues
        all_issues += check_new_log_errors(cfg['log_unit'], last_log_check.get(cfg['log_unit']))
        last_log_check[cfg['log_unit']] = now.isoformat()

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
    state['last_log_check'] = last_log_check
    save_state(state)


if __name__ == '__main__':
    main()
