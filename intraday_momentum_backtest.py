"""Backtest the momentum/breakout intraday signal (intraday_momentum.py).

Reuses intraday_backtest.py's machinery unchanged -- the same no-lookahead
as-of windowing, the same time-ordered train/holdout split, the same fixed
percentage exit -- so the ONLY variable that differs from the run that
produced a decisive negative on 2026-09-16 is the entry signal itself.

Pre-registered pass criteria, fixed before this was first executed (the
point of LESSONS 24: the first two intraday attempts each "passed" a naive
expectancy>0 check on n=7 and n=10 and meant nothing, because the bar was
chosen after seeing the numbers):

  1. holdout n >= 50                     -- enough of a sample to decide on
  2. holdout expectancy > 0              -- makes money out of sample
  3. holdout win rate >= break-even      -- 1/(1+target_r) = 40% at 1.5R
  4. majority of symbols positive        -- not one symbol carrying it,
                                            the failure mode LESSONS 8
                                            (and the veto audit) both hit

All four must hold to proceed to a live session runner. Anything else is a
negative or inconclusive result and stops here.

Run with: venv/bin/python intraday_momentum_backtest.py
"""
import statistics
from datetime import timedelta

from alpaca_client import AlpacaClient
from config import settings
from intraday_backtest import (MIN_HOLDOUT_TRADES, MIN_WARMUP_BARS, SIGNAL_BAR_WINDOW,
                                STOP_PCT_CANDIDATES, TARGET_R, _as_of_window, _parse_ts,
                                expectancy, split_train_holdout, win_rate)
from intraday_momentum import MIN_WARMUP_1H, evaluate, trend_up_1h
from intraday_signals import check_exit

BREAK_EVEN_WIN_RATE = 1.0 / (1.0 + TARGET_R)


def fetch(client, symbol):
    """1h is needed again here -- unlike the mean-reversion version, which
    dropped its trend filter, the 1h regime state is half this signal."""
    return (client.get_bars(symbol, timeframe='1Hour', limit=10000),
            client.get_bars(symbol, timeframe='5Min', limit=10000))


def simulate_symbol(symbol, bars_1h, bars_5m, stop_pct, target_r=TARGET_R):
    """One position at a time per symbol, no pyramiding -- matches both the
    live convention and the previous intraday run."""
    ts_1h = [_parse_ts(b) for b in bars_1h]
    closes_1h = [b['c'] for b in bars_1h]

    trades = []
    position = None

    for i, bar in enumerate(bars_5m):
        t = _parse_ts(bar)
        price = bar['c']

        if position is not None:
            outcome = check_exit(position['entry_price'], price, stop_pct, target_r)
            if outcome is not None:
                trades.append({
                    'symbol': symbol, 'entry_time': position['entry_time'],
                    'entry_price': position['entry_price'], 'exit_price': price,
                    'pct_return': (price - position['entry_price']) / position['entry_price'],
                    'outcome': outcome,
                })
                position = None
            continue

        window_5m = [b['c'] for b in bars_5m[max(0, i - SIGNAL_BAR_WINDOW + 1):i + 1]]
        if len(window_5m) < MIN_WARMUP_BARS:
            continue

        # Only 1h bars whose own period has fully closed by this 5m bar --
        # this is where lookahead would otherwise leak in.
        as_of_1h = _as_of_window(ts_1h, closes_1h, timedelta(hours=1), t, window=SIGNAL_BAR_WINDOW)
        if len(as_of_1h) < MIN_WARMUP_1H:
            continue

        if evaluate(symbol, window_5m, trend_ok=trend_up_1h(as_of_1h)) is not None:
            position = {'entry_time': t, 'entry_price': price}

    return trades


def main():
    print(__doc__.split('Run with:')[0].strip())
    print('=' * 72)

    watchlist = settings.WATCHLIST
    client = AlpacaClient()
    print(f'Fetching 1h/5m bars for {len(watchlist)} symbols...')
    data = {}
    for sym in watchlist:
        b1h, b5m = fetch(client, sym)
        data[sym] = (b1h, b5m)
        print(f'  {sym}: {len(b1h)} 1h bars, {len(b5m)} 5m bars')

    print('\n=== Selecting stop_pct on TRAIN only ===')
    best_stop, best_train_exp = None, None
    for stop_pct in STOP_PCT_CANDIDATES:
        all_trades = []
        for sym, (b1h, b5m) in data.items():
            all_trades += simulate_symbol(sym, b1h, b5m, stop_pct)
        train, _ = split_train_holdout(all_trades)
        exp = expectancy(train) if train else 0.0
        print(f'  stop_pct={stop_pct:.3f}: {len(all_trades)} total trades, '
              f'train n={len(train)}, train expectancy={exp:.3f}%/trade')
        if best_train_exp is None or exp > best_train_exp:
            best_stop, best_train_exp = stop_pct, exp

    print(f'\n=== Chosen stop_pct={best_stop} (best on TRAIN) -- checking HOLDOUT once ===')
    all_trades = []
    for sym, (b1h, b5m) in data.items():
        all_trades += simulate_symbol(sym, b1h, b5m, best_stop)
    train, holdout = split_train_holdout(all_trades)
    print(f'TRAIN:   n={len(train)}, expectancy={expectancy(train):.3f}%/trade, '
          f'win_rate={win_rate(train):.1f}%')
    print(f'HOLDOUT: n={len(holdout)}, expectancy={expectancy(holdout):.3f}%/trade, '
          f'win_rate={win_rate(holdout):.1f}%')
    print(f'(break-even win rate at {TARGET_R}R = {BREAK_EVEN_WIN_RATE*100:.1f}%)')

    by_symbol = {}
    for tr in holdout:
        by_symbol.setdefault(tr['symbol'], []).append(tr)
    print('\n=== Per-symbol holdout ===')
    positive = 0
    for sym in sorted(by_symbol):
        e = expectancy(by_symbol[sym])
        positive += e > 0
        print(f'  {sym:6} n={len(by_symbol[sym]):<4} expectancy={e:+.3f}%/trade')

    print('\n=== Verdict against the pre-registered criteria ===')
    c1 = len(holdout) >= MIN_HOLDOUT_TRADES
    c2 = expectancy(holdout) > 0
    c3 = win_rate(holdout) >= BREAK_EVEN_WIN_RATE * 100
    c4 = by_symbol and positive > len(by_symbol) / 2
    for label, ok in [
        (f'1. holdout n >= {MIN_HOLDOUT_TRADES}', c1),
        ('2. holdout expectancy > 0', c2),
        (f'3. holdout win rate >= {BREAK_EVEN_WIN_RATE*100:.1f}%', c3),
        (f'4. majority of symbols positive ({positive}/{len(by_symbol)})', c4),
    ]:
        print(f'  [{"PASS" if ok else "FAIL"}] {label}')

    if all([c1, c2, c3, c4]):
        print('\nALL CRITERIA MET -- proceed to the live session runner.')
    elif not c1:
        print('\nINCONCLUSIVE -- too few holdout trades to decide anything.')
    else:
        print('\nNEGATIVE RESULT -- do not deploy. This is an answer, not a failure.')


if __name__ == '__main__':
    main()
