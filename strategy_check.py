"""Standalone VPS strategy check: runs hourly to verify the live scanner is
producing sane signals, and once a day re-backtests the current thresholds
against fresh real bars — an alert-only tool in service of the project goal
(iteratively refine the strategy from real data), not a trading job.

Meant to run via crontab hourly on the VPS itself, independent of any
Claude Code session — same reasoning as watchdog.py: the existing hourly
"Strategy Review" cloud routine can't reach data.alpaca.markets at all
(persistently network-blocked in that sandbox), so it can never actually
backtest. Only the VPS (and a dev/interactive session) has real bar access.

Two checks, both alert-only via WhatsApp (never trades, never edits
thresholds) with the same cooldown/state pattern as watchdog.py:

- Hourly: re-run the live scanner against the whole watchlist. Flags a
  data outage (too few bars / scanner error) or a SELL signal that's
  persisted for 2+ consecutive hourly checks on a symbol still held
  (the scheduler should have closed it well before then).
- Once/day, after market close: re-backtest BUY_RSI_MAX/SELL_RSI_MIN
  (read live from scanner.py, so this never drifts from what's actually
  deployed) against a fresh 300-bar window per symbol, using the same
  continuous-EMA methodology as every backtest in STRATEGY.md. Flags if
  aggregate expectancy has gone negative, if it's become outlier-driven
  (a leave-one-symbol-out flips sign — see project Lesson #10), or if
  it's dropped more than half since the last recorded run.
- Same daily run, once >= FORWARD_TEST_MIN_TRADES real closed trades
  exist (trader.py persists every closed trade's realized P&L to
  trade_history.json — see its _record_trade_outcome): compares the
  live/forward-test win rate and expectancy against this same backtest.
  Flags if the real results are negative, or less than half the
  backtest's predicted expectancy — the actual "does live match what we
  backtested" check the project's goal (STRATEGY.md) has been waiting on
  since before real trades existed to compare against.
"""
import json
import os
from datetime import datetime, timezone

from alpaca_client import AlpacaClient, position_symbol
from scanner import OpportunityScanner, _compute_ema, _compute_rsi
from whatsapp_notifier import WhatsAppNotifier
from config import settings

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'strategy_check_state.json')
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trade_history.json')
HEALTH_ALERT_COOLDOWN_SECONDS = 2 * 60 * 60
BACKTEST_ALERT_COOLDOWN_SECONDS = 24 * 60 * 60
BACKTEST_LOOKBACK_BARS = 300
STUCK_SELL_THRESHOLD = 2  # consecutive hourly checks with an unclosed SELL signal
FORWARD_TEST_MIN_TRADES = 10  # below this, real trade count is too thin to compare against the backtest at all


def load_trade_history():
    if os.path.exists(TRADE_HISTORY_FILE):
        try:
            with open(TRADE_HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _forward_test_stats(trade_history, asset_class):
    """Realized win rate/expectancy from trader.py's persisted trade_history.json
    (real closed trades only), split by asset class to match how the backtest is
    split. Returns trade_count always (so progress toward FORWARD_TEST_MIN_TRADES is
    visible even before there's enough to compare), plus expectancy/win_rate once
    the threshold is met."""
    trades = [t for t in trade_history if t.get('asset_class') == asset_class and t.get('pnl_pct') is not None]
    stats = {'trade_count': len(trades), 'ready': len(trades) >= FORWARD_TEST_MIN_TRADES}
    if stats['ready']:
        pcts = [t['pnl_pct'] for t in trades]
        stats['expectancy_pct'] = (sum(pcts) / len(pcts)) * 100
        stats['win_rate_pct'] = (sum(1 for p in pcts if p > 0) / len(pcts)) * 100
    return stats


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'active_alerts': {}, 'stuck_sell_counts': {}, 'last_backtest_date': None, 'backtest_history': []}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def check_signal_health(client, watchlist, state, held_symbols):
    scanner = OpportunityScanner(client)
    results = scanner.scan(watchlist)
    issues = []
    stuck = state.setdefault('stuck_sell_counts', {})
    seen = set()

    for r in results:
        symbol = r['symbol']
        seen.add(symbol)
        if r['signal'] == 'error':
            issues.append((f'data_error:{symbol}', f'Scanner error on {symbol}: {r["reason"]}'))
            stuck.pop(symbol, None)
            continue
        if r['reason'] == 'insufficient history':
            issues.append((f'data_gap:{symbol}', f'Only got insufficient bar history for {symbol} — possible data outage'))
            stuck.pop(symbol, None)
            continue

        held = position_symbol(symbol) in held_symbols
        if r['signal'] == 'sell' and held:
            stuck[symbol] = stuck.get(symbol, 0) + 1
            if stuck[symbol] >= STUCK_SELL_THRESHOLD:
                issues.append((
                    f'stuck_sell:{symbol}',
                    f'{symbol} has shown a SELL signal for {stuck[symbol]} consecutive hourly checks '
                    f'while still held ({r["reason"]}) — scheduler may not be closing it'
                ))
        else:
            stuck.pop(symbol, None)

    for symbol in list(stuck.keys()):
        if symbol not in seen:
            stuck.pop(symbol)

    return issues


def _simulate_trades(bars, buy_rsi_max, sell_rsi_min):
    """Walks one continuous EMA9/EMA21 series over the whole fetched window and
    simulates buy/sell day-by-day — the same methodology used for every backtest
    recorded in STRATEGY.md (confirmed by reproducing the documented 65/80 result:
    not outlier-driven, +0.5%-+1.9%/trade leave-one-symbol-out).

    This now closely matches live per-check behavior too: OpportunityScanner._analyze
    fetches SIGNAL_BAR_WINDOW (90) bars and recomputes EMA9/21 from that window each
    check — originally 35 bars, which left EMA21 undercooked and gave a measurably
    weaker, outlier-driven result versus this continuous methodology (a real gap,
    found and fixed 2026-07-16 by raising the window to 90, the point where the two
    methodologies converge to identical results). See STRATEGY.md "Automated
    monitoring" for the investigation and convergence data."""
    closes = [float(b['c']) for b in bars]
    ema9 = _compute_ema(closes, 9)
    ema21 = _compute_ema(closes, 21)
    if len(ema9) < 2 or len(ema21) < 2:
        return []

    offset9 = len(closes) - len(ema9)
    offset21 = len(closes) - len(ema21)
    trades = []
    in_position = False
    entry_price = 0.0

    for i in range(max(offset9, offset21) + 1, len(closes)):
        i9 = i - offset9
        i21 = i - offset21
        prev_diff = ema9[i9 - 1] - ema21[i21 - 1]
        curr_diff = ema9[i9] - ema21[i21]
        rsi = _compute_rsi(closes[max(0, i - 40):i + 1])
        price = closes[i]

        if not in_position:
            if prev_diff < 0 and curr_diff > 0 and rsi < buy_rsi_max:
                in_position = True
                entry_price = price
        else:
            crossunder = prev_diff > 0 and curr_diff < 0
            if crossunder or rsi > sell_rsi_min:
                trades.append((price - entry_price) / entry_price)
                in_position = False

    return trades


def _backtest_watchlist(client, watchlist):
    bars_by_symbol = client.get_bars_multi(watchlist, limit=BACKTEST_LOOKBACK_BARS)
    per_symbol = {}
    for symbol in watchlist:
        bars = bars_by_symbol.get(symbol, [])
        per_symbol[symbol] = _simulate_trades(bars, OpportunityScanner.BUY_RSI_MAX, OpportunityScanner.SELL_RSI_MIN)

    all_trades = [t for trades in per_symbol.values() for t in trades]
    if not all_trades:
        return None

    expectancy_pct = (sum(all_trades) / len(all_trades)) * 100
    win_rate_pct = (sum(1 for t in all_trades if t > 0) / len(all_trades)) * 100
    leave_one_out = {}
    for excluded in watchlist:
        remaining = [t for sym, ts in per_symbol.items() if sym != excluded for t in ts]
        if remaining:
            leave_one_out[excluded] = (sum(remaining) / len(remaining)) * 100

    return {
        'trade_count': len(all_trades),
        'expectancy_pct': expectancy_pct,
        'win_rate_pct': win_rate_pct,
        'leave_one_out': leave_one_out,
    }


def run_daily_backtests(client, state):
    issues = []
    history = state.setdefault('backtest_history', [])
    today = datetime.now(timezone.utc).date().isoformat()
    trade_history = load_trade_history()

    for label, watchlist, asset_class in (
        ('stock', settings.WATCHLIST, 'us_equity'),
        ('crypto', settings.CRYPTO_WATCHLIST, 'crypto'),
    ):
        if not watchlist:
            continue
        result = _backtest_watchlist(client, watchlist)
        if result is None:
            continue
        result['label'] = label
        result['date'] = today

        aggregate_positive = result['expectancy_pct'] > 0
        if not aggregate_positive:
            issues.append((
                f'backtest_negative:{label}',
                f'{label} backtest expectancy went negative: {result["expectancy_pct"]:.3f}%/trade '
                f'over {result["trade_count"]} trades ({BACKTEST_LOOKBACK_BARS}-bar window)'
            ))

        flipped = [sym for sym, exp in result['leave_one_out'].items() if (exp > 0) != aggregate_positive]
        if flipped and aggregate_positive:
            issues.append((
                f'backtest_outlier:{label}',
                f'{label} backtest aggregate (+{result["expectancy_pct"]:.3f}%/trade) is outlier-driven — '
                f'flips sign excluding: {", ".join(flipped)}'
            ))

        prior = next((h for h in reversed(history) if h.get('label') == label), None)
        if prior and prior['expectancy_pct'] > 0 and result['expectancy_pct'] < prior['expectancy_pct'] * 0.5:
            issues.append((
                f'backtest_degraded:{label}',
                f'{label} backtest expectancy dropped from {prior["expectancy_pct"]:.3f}%/trade '
                f'({prior["date"]}) to {result["expectancy_pct"]:.3f}%/trade — more than 50% weaker'
            ))

        # Forward test: compare real closed-trade results against this same backtest,
        # once there's enough real trades to mean anything (per-project convention,
        # see STRATEGY.md -- fewer than ~10 closed trades is noise, not signal).
        forward = _forward_test_stats(trade_history, asset_class)
        result['forward_test'] = forward
        if forward['ready']:
            if forward['expectancy_pct'] <= 0:
                issues.append((
                    f'forward_test_negative:{label}',
                    f'{label} LIVE forward-test expectancy is negative: {forward["expectancy_pct"]:+.3f}%/trade '
                    f'over {forward["trade_count"]} real closed trades (backtest predicts '
                    f'{result["expectancy_pct"]:+.3f}%/trade) — worth a closer look'
                ))
            elif aggregate_positive and forward['expectancy_pct'] < result['expectancy_pct'] * 0.5:
                issues.append((
                    f'forward_test_diverged:{label}',
                    f'{label} LIVE forward-test expectancy ({forward["expectancy_pct"]:+.3f}%/trade, '
                    f'{forward["trade_count"]} real trades) is less than half the backtest prediction '
                    f'({result["expectancy_pct"]:+.3f}%/trade) — may be worth revisiting thresholds'
                ))

        history.append(result)

    state['backtest_history'] = history[-60:]  # ~2 months of daily runs
    state['last_backtest_date'] = today
    return issues


def main():
    state = load_state()
    active = state.setdefault('active_alerts', {})
    now = datetime.now(timezone.utc)

    client = AlpacaClient()
    held_symbols = {p['symbol'] for p in client.get_positions()}
    watchlist = settings.WATCHLIST + settings.CRYPTO_WATCHLIST

    all_issues = check_signal_health(client, watchlist, state, held_symbols)

    # Once/day, after market close (16:00 ET) with a comfortable buffer for the
    # daily bar to settle — 21:00 UTC covers ET's -4/-5 offset across DST.
    if state.get('last_backtest_date') != now.date().isoformat() and now.hour >= 21:
        all_issues += run_daily_backtests(client, state)

    current_keys = {key for key, _ in all_issues}
    messages = []
    for key, msg in all_issues:
        cooldown = BACKTEST_ALERT_COOLDOWN_SECONDS if key.startswith('backtest_') else HEALTH_ALERT_COOLDOWN_SECONDS
        last_sent = active.get(key)
        if last_sent is None:
            messages.append(msg)
            active[key] = now.isoformat()
        elif (now - datetime.fromisoformat(last_sent)).total_seconds() > cooldown:
            messages.append(f'[STILL ACTIVE] {msg}')
            active[key] = now.isoformat()

    for key in list(active.keys()):
        if key not in current_keys:
            del active[key]

    if messages:
        WhatsAppNotifier().send('AlpacaTradingBot Strategy Check', '\n\n'.join(messages))

    state['active_alerts'] = active
    save_state(state)


if __name__ == '__main__':
    main()
