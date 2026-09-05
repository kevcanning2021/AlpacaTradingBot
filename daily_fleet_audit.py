"""Daily fleet audit for Main + Sofi: fresh dual-signal backtest, forward-test
snapshot, and watchlist/threshold drift detection -- runs via crontab,
independent of any Claude Code session (same durability class as
watchdog.py/strategy_check.py; a CronCreate session job was considered first
but rejected -- it only fires while that one session stays open and is
deleted when it exits, so it can't be "daily, without asking" in any
reliable sense).

Added 2026-09-06 after the user asked for the 2026-09-05 Fleet Audit
(backtest/forward-test/trailing-stop review) to happen automatically every
day, with a persistent record so "give me a breakdown of the last run"
works in any future session without re-running everything. This script is
the durable, deterministic half of that -- it writes real numbers to
fleet_audit_log.json. The narrative half (interpreting drift, writing
recommendations, updating the shared Fleet Audit artifact) still needs an
active Claude session reading this file; see STRATEGY.md/project notes for
that split.

Deliberately does NOT touch trader.py's own state files or make any trading
decision -- read-only, alert-only, like every other script in this family.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from alpaca_client import AlpacaClient
from scanner import _compute_ema, _compute_rsi, _compute_bollinger, OpportunityScanner
from telegram_notifier import TelegramNotifier
from config import settings

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fleet_audit_log.json')
MAX_LOG_ENTRIES = 30
BACKTEST_BARS = 460

STOP_LOSS = settings.STOP_LOSS_THRESHOLD
TRAILING_STOP = settings.TRAILING_STOP_THRESHOLD
BUY_RSI_MAX = OpportunityScanner.BUY_RSI_MAX
SELL_RSI_MIN = OpportunityScanner.SELL_RSI_MIN
BOLL_PERIOD = OpportunityScanner.BOLLINGER_PERIOD
BOLL_STD = OpportunityScanner.BOLLINGER_STD
BOLL_OVERSOLD_RSI = OpportunityScanner.BOLLINGER_OVERSOLD_RSI


def simulate_dual_signal(closes):
    """Same walk-forward engine as the 2026-09-05 Fleet Audit: entry via
    Bollinger-or-EMA (Bollinger preferred on same-day tie, matching
    scanner.py: OpportunityScanner._analyze_bars), exit via the owning
    method's own sell rule OR the live 5%/8% stop/trail, whichever comes
    first. No reentry/position-sizing (pure %-return per trade) -- a
    deliberate simplification for a fast daily health check, not a replacement
    for a full STRATEGY.md-grade backtest."""
    ema9 = _compute_ema(closes, 9)
    ema21 = _compute_ema(closes, 21)
    mid, _, lower = _compute_bollinger(closes, BOLL_PERIOD, BOLL_STD)
    offset9 = len(closes) - len(ema9)
    offset21 = len(closes) - len(ema21)
    if len(ema9) < 2 or len(ema21) < 2:
        return []

    trades = []
    in_position = False
    method = None
    entry_price = peak = 0.0
    start = max(offset9, offset21, BOLL_PERIOD) + 1

    for i in range(start, len(closes)):
        price = closes[i]
        prev_price = closes[i - 1]
        i9, i21 = i - offset9, i - offset21
        rsi = _compute_rsi(closes[max(0, i - 40):i + 1])

        if not in_position:
            boll_buy = (lower[i] is not None and lower[i - 1] is not None
                        and prev_price < lower[i - 1] and price > lower[i] and rsi < BOLL_OVERSOLD_RSI)
            ema_buy = (ema9[i9 - 1] - ema21[i21 - 1] < 0 and ema9[i9] - ema21[i21] > 0 and rsi < BUY_RSI_MAX)
            if boll_buy:
                in_position, method, entry_price, peak = True, 'bollinger', price, price
            elif ema_buy:
                in_position, method, entry_price, peak = True, 'ema', price, price
        else:
            peak = max(peak, price)
            stop_hit = price <= entry_price * (1 - STOP_LOSS)
            trail_hit = price <= peak * (1 - TRAILING_STOP)
            if method == 'bollinger':
                sell_signal = price >= mid[i] or rsi > SELL_RSI_MIN
            else:
                sell_signal = (ema9[i9 - 1] - ema21[i21 - 1] > 0 and ema9[i9] - ema21[i21] < 0) or rsi > SELL_RSI_MIN
            if stop_hit or trail_hit or sell_signal:
                trades.append((price - entry_price) / entry_price)
                in_position = False
    return trades


def run_backtest(watchlist):
    client = AlpacaClient()
    bars_by_symbol = client.get_bars_multi(watchlist, limit=BACKTEST_BARS)
    all_trades = []
    for sym in watchlist:
        closes = [float(b['c']) for b in bars_by_symbol.get(sym, [])]
        all_trades += simulate_dual_signal(closes)
    if not all_trades:
        return {'trade_count': 0, 'win_rate_pct': None, 'expectancy_pct': None}
    wins = [t for t in all_trades if t > 0]
    return {
        'trade_count': len(all_trades),
        'win_rate_pct': round(len(wins) / len(all_trades) * 100, 1),
        'expectancy_pct': round(sum(all_trades) / len(all_trades) * 100, 3),
    }


def forward_test_snapshot():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_history.json')
    if not os.path.exists(path):
        return {'closed_trades': 0}
    with open(path) as f:
        trades = json.load(f)
    return {
        'closed_trades': len(trades),
        'last_trade': trades[-1]['timestamp'] if trades else None,
        'total_pnl': round(sum(t.get('pnl', 0) for t in trades), 2),
    }


def detect_drift(bot_label, watchlist, prior_entry):
    """Compares today's live WATCHLIST/threshold values against the most
    recent prior log entry (not against STRATEGY.md's prose, which isn't
    practical to parse here) -- catches a value CHANGING day-to-day, which
    is exactly the pattern that let Main's watchlist grow from 7 to 13
    symbols with nobody noticing. First run ever has nothing prior to
    compare against, so it reports clean rather than flagging everything."""
    current = {'watchlist': sorted(watchlist), 'buy_rsi_max': BUY_RSI_MAX, 'sell_rsi_min': SELL_RSI_MIN}
    if prior_entry is None:
        return {'changed': False, 'current': current}
    prior = prior_entry.get('config_snapshot', {})
    changed = prior.get('watchlist') != current['watchlist'] or \
        prior.get('buy_rsi_max') != current['buy_rsi_max'] or \
        prior.get('sell_rsi_min') != current['sell_rsi_min']
    return {'changed': changed, 'current': current, 'prior': prior}


def load_log():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_log(log):
    with open(LOG_FILE, 'w') as f:
        json.dump(log[-MAX_LOG_ENTRIES:], f, indent=2)


def main():
    log = load_log()
    prior_entry = log[-1] if log else None

    backtest = run_backtest(settings.WATCHLIST)
    forward = forward_test_snapshot()
    drift = detect_drift('Main', settings.WATCHLIST, prior_entry)

    entry = {
        'date': datetime.now(timezone.utc).date().isoformat(),
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'backtest': backtest,
        'forward_test': forward,
        'config_snapshot': drift['current'],
        'drift_detected': drift['changed'],
    }
    log.append(entry)
    save_log(log)

    lines = [
        f"Daily audit ({entry['date']}): backtest {backtest['trade_count']} trades, "
        f"{backtest['win_rate_pct']}% win, {backtest['expectancy_pct']:+.2f}%/trade" if backtest['trade_count'] else
        f"Daily audit ({entry['date']}): backtest produced 0 trades (check data feed)",
        f"Forward test: {forward['closed_trades']} real closed trades, ${forward.get('total_pnl', 0):+.2f} total P&L",
    ]
    if drift['changed']:
        lines.append(f"WATCHLIST/threshold drift detected since last run: {drift['prior']} -> {drift['current']}")

    message = '\n'.join(lines)
    print(message)
    if drift['changed']:
        TelegramNotifier().send('Daily Fleet Audit', message)


if __name__ == '__main__':
    main()
