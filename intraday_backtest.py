"""Backtest for the Sofi Intraday multi-timeframe strategy (Phase 2 of the
approved plan) -- the real gate deciding whether Phase 3/4 (live session
runner + dashboard trigger) get built at all.

Walks forward through 5-minute bars only (the fastest timeframe drives
timing), looking up the most recently CLOSED 1h/15m bar as of each 5m
bar's timestamp via bisect -- a 1h bar timestamped 14:00 only counts once
its own period has actually elapsed (>= 15:00), same for 15m bars. This is
the discipline that keeps the simulation honest: no bar counts as evidence
before it could actually have been observed live.

Each timeframe is windowed to the trailing SIGNAL_BAR_WINDOW=90 bars before
being handed to evaluate() -- the same bounded-window convention
scanner.py's live OpportunityScanner.scan() already uses (limit=
SIGNAL_BAR_WINDOW). This isn't just a performance optimization (recomputing
indicators over an ever-growing full history would make this O(n^2)); it's
what makes the backtest match what the live session runner will actually
see, since Phase 3 will fetch the same bounded recent window, not full
history, on every check.

Stop/target selection follows the exact discipline in LESSONS.md entry 17:
a small set of stop_pct candidates is tried, but the winner is chosen using
TRAIN data only, then that ONE chosen configuration is checked against
HOLDOUT exactly once -- never report whichever candidate happens to look
best on holdout.

Train/holdout split matches this project's established convention: all
trades across all symbols pooled and sorted by entry time, first half by
trade INDEX (not by symbol, not by calendar date) is train, second half is
holdout.

Run with: venv/bin/python intraday_backtest.py
"""
import bisect
from datetime import datetime, timedelta

from alpaca_client import AlpacaClient
from config import settings
from intraday_signals import evaluate, check_exit, setup_15m, ARMED_WINDOW_5M_BARS

STOP_PCT_CANDIDATES = [0.003, 0.005, 0.008, 0.01]
TARGET_R = 1.5
MIN_HOLDOUT_TRADES = 50  # pre-registered before the armed-window run -- see the verdict block
SIGNAL_BAR_WINDOW = 90  # matches scanner.py's OpportunityScanner.SIGNAL_BAR_WINDOW
MIN_WARMUP_BARS = 25    # enough to seed EMA21/Bollinger(20) with a little room


def _parse_ts(bar):
    return datetime.fromisoformat(bar['t'].replace('Z', '+00:00'))


def fetch_all_timeframes(client, symbol):
    """1h is no longer fetched -- the trend filter was dropped 2026-09-16,
    so pulling it was a third of the fetch time for data nothing reads."""
    bars_15m = client.get_bars(symbol, timeframe='15Min', limit=10000)
    bars_5m = client.get_bars(symbol, timeframe='5Min', limit=10000)
    return bars_15m, bars_5m


def _as_of_window(timestamps, closes, duration, as_of, window=SIGNAL_BAR_WINDOW):
    """Trailing `window` closes of every bar whose own period has fully
    elapsed by `as_of` (bar timestamp + duration <= as_of) -- no lookahead,
    and bounded the same way the live scanner's own request would be."""
    cutoff = as_of - duration
    idx = bisect.bisect_right(timestamps, cutoff)
    return closes[max(0, idx - window):idx]


def simulate_symbol(symbol, bars_15m, bars_5m, stop_pct, target_r=TARGET_R):
    """Returns a list of trade dicts. One open position at a time per
    symbol (no pyramiding), matching this fleet's existing convention.
    1h bars are no longer consulted at all -- the trend filter was dropped
    2026-09-16 (see intraday_signals.evaluate)."""
    ts_15m = [_parse_ts(b) for b in bars_15m]
    closes_15m = [b['c'] for b in bars_15m]

    trades = []
    position = None
    # Owns the arming state on behalf of evaluate() (which stays pure) --
    # a 15m setup arms the symbol for ARMED_WINDOW_5M_BARS subsequent 5m
    # bars rather than requiring the 5m trigger to land on the exact same
    # bar. See intraday_signals.evaluate()'s docstring for why.
    armed_countdown = 0

    for i, bar in enumerate(bars_5m):
        t = _parse_ts(bar)
        price = bar['c']

        if position is not None:
            outcome = check_exit(position['entry_price'], price, stop_pct, target_r)
            if outcome is not None:
                pct_return = (price - position['entry_price']) / position['entry_price']
                trades.append({
                    'symbol': symbol, 'entry_time': position['entry_time'],
                    'entry_price': position['entry_price'], 'exit_price': price,
                    'pct_return': pct_return, 'outcome': outcome,
                })
                position = None
            continue  # one position at a time -- don't also evaluate a fresh entry on this bar

        window_5m = [b2['c'] for b2 in bars_5m[max(0, i - SIGNAL_BAR_WINDOW + 1):i + 1]]
        if len(window_5m) < MIN_WARMUP_BARS:
            continue

        as_of_15m = _as_of_window(ts_15m, closes_15m, timedelta(minutes=15), t)
        if len(as_of_15m) < MIN_WARMUP_BARS:
            continue

        if setup_15m(as_of_15m):
            armed_countdown = ARMED_WINDOW_5M_BARS
        elif armed_countdown > 0:
            armed_countdown -= 1

        signal = evaluate(symbol, window_5m, setup_active=armed_countdown > 0)
        if signal is not None:
            position = {'entry_time': t, 'entry_price': price}
            armed_countdown = 0  # consumed -- don't re-enter off the same setup

    return trades


def split_train_holdout(trades):
    trades_sorted = sorted(trades, key=lambda tr: tr['entry_time'])
    mid = len(trades_sorted) // 2
    return trades_sorted[:mid], trades_sorted[mid:]


def expectancy(trades):
    if not trades:
        return 0.0
    return sum(tr['pct_return'] for tr in trades) / len(trades)


def win_rate(trades):
    if not trades:
        return 0.0
    return sum(1 for tr in trades if tr['pct_return'] > 0) / len(trades) * 100


def main():
    client = AlpacaClient()
    watchlist = settings.WATCHLIST
    print(f"Fetching 15m/5m bars for {len(watchlist)} symbols...")

    per_symbol_bars = {}
    for symbol in watchlist:
        try:
            per_symbol_bars[symbol] = fetch_all_timeframes(client, symbol)
            b15m, b5m = per_symbol_bars[symbol]
            print(f"  {symbol}: {len(b15m)} 15m bars, {len(b5m)} 5m bars")
        except Exception as e:
            print(f"  {symbol}: fetch failed ({e}), skipping")

    print("\n=== Selecting stop_pct on TRAIN only ===")
    best_stop_pct, best_train_expectancy = None, float('-inf')
    for stop_pct in STOP_PCT_CANDIDATES:
        all_trades = []
        for symbol, (b15m, b5m) in per_symbol_bars.items():
            all_trades += simulate_symbol(symbol, b15m, b5m, stop_pct)
        train, _holdout = split_train_holdout(all_trades)
        train_exp = expectancy(train)
        print(f"  stop_pct={stop_pct:.3f}: {len(all_trades)} total trades, "
              f"train n={len(train)}, train expectancy={train_exp * 100:.3f}%/trade")
        if train_exp > best_train_expectancy:
            best_train_expectancy = train_exp
            best_stop_pct = stop_pct

    print(f"\n=== Chosen stop_pct={best_stop_pct} (best on TRAIN) -- checking HOLDOUT once ===")
    all_trades = []
    for symbol, (b15m, b5m) in per_symbol_bars.items():
        all_trades += simulate_symbol(symbol, b15m, b5m, best_stop_pct)
    train, holdout = split_train_holdout(all_trades)

    print(f"TRAIN:   n={len(train)}, expectancy={expectancy(train) * 100:.3f}%/trade, win_rate={win_rate(train):.1f}%")
    print(f"HOLDOUT: n={len(holdout)}, expectancy={expectancy(holdout) * 100:.3f}%/trade, win_rate={win_rate(holdout):.1f}%")

    print("\n=== Per-symbol holdout contribution (robustness check) ===")
    by_symbol = {}
    for tr in holdout:
        by_symbol.setdefault(tr['symbol'], []).append(tr)
    for symbol, trs in sorted(by_symbol.items()):
        print(f"  {symbol}: n={len(trs)}, expectancy={expectancy(trs) * 100:.3f}%/trade")

    print("\n=== Verdict ===")
    # MIN_HOLDOUT_TRADES was raised 5 -> 50 and pre-registered BEFORE this
    # run, deliberately. The first two attempts both cleared the old
    # threshold on n=7 and n=10 and still meant nothing -- at that size a
    # positive expectancy is indistinguishable from a coin flip, so the
    # old bar let a non-answer look like a pass. The whole point of this
    # third attempt is sample size, so the criterion has to be sample size.
    if len(holdout) < MIN_HOLDOUT_TRADES:
        print(f"INCONCLUSIVE: holdout n={len(holdout)} < {MIN_HOLDOUT_TRADES} -- still not enough "
              f"trades to judge either way, regardless of the expectancy number above. "
              f"The armed-window change did not fix the sample-size problem.")
    elif expectancy(holdout) > 0:
        print(f"PASS: holdout n={len(holdout)} with positive expectancy "
              f"({expectancy(holdout) * 100:.3f}%/trade) -- a real, usable result. "
              f"Worth proceeding to Phase 3 (live session runner).")
    else:
        print(f"CLEAR NEGATIVE: holdout n={len(holdout)} with expectancy "
              f"{expectancy(holdout) * 100:.3f}%/trade -- a real answer, and the answer is no. "
              f"Do not build live capability on this rule.")


if __name__ == '__main__':
    main()
