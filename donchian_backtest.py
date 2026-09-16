"""Does adding a THIRD independent signal (Donchian breakout) to Main's
existing Bollinger+EMA dual-signal raise trade frequency without costing
expectancy?

Why this and not another threshold tweak: every previous attempt to make
Main trade more often loosened or widened the EXISTING signals (three
watchlist widenings, hourly bars, a looser RSI gate) and every one was
rejected -- see STRATEGY.md. The one change that did work was adding an
independent signal FAMILY: going EMA-only -> EMA+Bollinger took the
backtest from 44 to 90 trades while slightly IMPROVING expectancy
(+1.66% -> +1.71%/trade). That lever has been pulled exactly once. This
pulls it a second time.

Donchian is chosen because it is genuinely orthogonal to both incumbents:
Bollinger bets on reversion TO the mean, EMA-crossover bets on trend
continuation, breakout bets on momentum THROUGH resistance. Different
premises should fire on different days rather than duplicating each
other's entries -- which is the whole point, since a correlated third
signal would add trades that the existing two would mostly have taken
anyway.

Deliberate design choices, stated up front so they aren't mistaken for
oversights:

- **Closes-based Donchian**, not high/low-based. The existing walk-forward
  engine (daily_fleet_audit.simulate_dual_signal) operates on closes only;
  using closes keeps this a strict superset of that engine so the baseline
  comparison is genuinely apples-to-apples. Closes-based channels are also
  less sensitive to single-print intraday wicks.
- **No RSI gate on the Donchian entry.** The other two signals both gate on
  RSI < 65, but a breakout to an N-day high is an overbought condition BY
  CONSTRUCTION -- applying BUY_RSI_MAX here would reject nearly every
  breakout and quietly test nothing. Momentum continuation is the premise;
  fighting it with a mean-reversion filter would be incoherent.
- **Donchian is evaluated LAST** (Bollinger -> EMA -> Donchian). The two
  incumbents keep first refusal exactly as they have today, so every trade
  Donchian contributes is strictly incremental and the baseline's behaviour
  is unchanged. That makes "did trade count go up" a clean question.

PRE-REGISTERED PASS/FAIL, fixed before this was first run (see LESSONS.md
entries 23-24 -- deciding the bar after seeing the numbers is how you talk
yourself into a bad result):
  1. Trade count must rise materially: >= +50% vs the dual-signal baseline
  2. Holdout expectancy must NOT fall below the baseline's own holdout
  3. Donchian period N selected on TRAIN only, then holdout checked once
Adding trades while degrading expectancy is a REJECTION, not a trade-off
to be argued about afterwards.

Run with: venv/bin/python donchian_backtest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alpaca_client import AlpacaClient
from scanner import _compute_ema, _compute_rsi, _compute_bollinger, OpportunityScanner
from config import settings

BACKTEST_BARS = 460
STOP_LOSS = settings.STOP_LOSS_THRESHOLD
TRAILING_STOP = settings.TRAILING_STOP_THRESHOLD
BUY_RSI_MAX = OpportunityScanner.BUY_RSI_MAX
SELL_RSI_MIN = OpportunityScanner.SELL_RSI_MIN
BOLL_PERIOD = OpportunityScanner.BOLLINGER_PERIOD
BOLL_STD = OpportunityScanner.BOLLINGER_STD
BOLL_OVERSOLD_RSI = OpportunityScanner.BOLLINGER_OVERSOLD_RSI

# Classic Donchian lengths (20 and 55 are the original Turtle periods).
# Selected on TRAIN only -- never by whichever looks best on holdout.
DONCHIAN_PERIODS = [10, 20, 40, 55]

MIN_TRADE_COUNT_UPLIFT = 1.50  # pre-registered: >= +50% vs baseline


def simulate(closes, donchian_period=None):
    """Walk-forward engine extended from daily_fleet_audit.simulate_dual_signal.
    With donchian_period=None this is byte-for-byte the existing dual-signal
    behaviour (the baseline); with a period set, Donchian is added as a third
    entry evaluated only after the other two decline.

    Returns a list of (entry_index, pct_return, method) so trades can be
    pooled across symbols and split chronologically.
    """
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
    entry_idx = 0
    warmup = max(offset9, offset21, BOLL_PERIOD) + 1
    if donchian_period:
        warmup = max(warmup, donchian_period + 1)

    for i in range(warmup, len(closes)):
        price = closes[i]
        prev_price = closes[i - 1]
        i9, i21 = i - offset9, i - offset21
        rsi = _compute_rsi(closes[max(0, i - 40):i + 1])

        if not in_position:
            boll_buy = (lower[i] is not None and lower[i - 1] is not None
                        and prev_price < lower[i - 1] and price > lower[i] and rsi < BOLL_OVERSOLD_RSI)
            ema_buy = (ema9[i9 - 1] - ema21[i21 - 1] < 0 and ema9[i9] - ema21[i21] > 0 and rsi < BUY_RSI_MAX)
            # Breakout above the highest close of the prior N bars. No RSI
            # gate -- see module docstring.
            donch_buy = False
            if donchian_period:
                prior_high = max(closes[i - donchian_period:i])
                donch_buy = price > prior_high

            if boll_buy:
                in_position, method, entry_price, peak, entry_idx = True, 'bollinger', price, price, i
            elif ema_buy:
                in_position, method, entry_price, peak, entry_idx = True, 'ema', price, price, i
            elif donch_buy:
                in_position, method, entry_price, peak, entry_idx = True, 'donchian', price, price, i
        else:
            peak = max(peak, price)
            stop_hit = price <= entry_price * (1 - STOP_LOSS)
            trail_hit = price <= peak * (1 - TRAILING_STOP)
            if method == 'bollinger':
                sell_signal = price >= mid[i] or rsi > SELL_RSI_MIN
            elif method == 'ema':
                sell_signal = (ema9[i9 - 1] - ema21[i21 - 1] > 0 and ema9[i9] - ema21[i21] < 0) or rsi > SELL_RSI_MIN
            else:
                # Classic Donchian channel exit: close below the lowest close
                # of the prior N bars. The shared stop/trail still apply as a
                # backstop, same as every other method here.
                prior_low = min(closes[i - donchian_period:i])
                sell_signal = price < prior_low or rsi > SELL_RSI_MIN
            if stop_hit or trail_hit or sell_signal:
                trades.append((entry_idx, (price - entry_price) / entry_price, method))
                in_position = False
    return trades


def run_all(bars_by_symbol, donchian_period=None):
    pooled = []
    for symbol, bars in bars_by_symbol.items():
        closes = [b['c'] for b in bars]
        for entry_idx, ret, method in simulate(closes, donchian_period):
            pooled.append({'symbol': symbol, 'entry_idx': entry_idx, 'ret': ret, 'method': method})
    return pooled


def split_train_holdout(trades):
    """Bucket by entry_idx against the midpoint of the bar series -- the
    same convention as this project's other backtests (a trade entered in
    the first half of the window is train, second half is holdout)."""
    train = [t for t in trades if t['entry_idx'] < BACKTEST_BARS / 2]
    holdout = [t for t in trades if t['entry_idx'] >= BACKTEST_BARS / 2]
    return train, holdout


def expectancy(trades):
    return sum(t['ret'] for t in trades) / len(trades) if trades else 0.0


def win_rate(trades):
    return sum(1 for t in trades if t['ret'] > 0) / len(trades) * 100 if trades else 0.0


def describe(label, trades):
    print(f"{label:<22} n={len(trades):<4} expectancy={expectancy(trades) * 100:+.3f}%/trade  win={win_rate(trades):.1f}%")


def main():
    client = AlpacaClient()
    watchlist = settings.WATCHLIST
    print(f"Fetching {BACKTEST_BARS} daily bars for {len(watchlist)} symbols...")
    bars_by_symbol = {}
    for symbol in watchlist:
        try:
            bars = client.get_bars(symbol, timeframe='1Day', limit=BACKTEST_BARS)
            if len(bars) >= 100:
                bars_by_symbol[symbol] = bars
            print(f"  {symbol}: {len(bars)} bars")
        except Exception as e:
            print(f"  {symbol}: fetch failed ({e}), skipping")

    print("\n=== BASELINE (current live behaviour: Bollinger + EMA) ===")
    base = run_all(bars_by_symbol, donchian_period=None)
    base_train, base_holdout = split_train_holdout(base)
    describe("baseline ALL", base)
    describe("baseline TRAIN", base_train)
    describe("baseline HOLDOUT", base_holdout)

    print("\n=== Selecting Donchian N on TRAIN only ===")
    best_n, best_train_exp = None, float('-inf')
    for n in DONCHIAN_PERIODS:
        trades = run_all(bars_by_symbol, donchian_period=n)
        train, _ = split_train_holdout(trades)
        exp = expectancy(train)
        added = len(trades) - len(base)
        print(f"  N={n:<3} total n={len(trades):<4} (+{added} vs baseline)  "
              f"train n={len(train):<4} train expectancy={exp * 100:+.3f}%/trade")
        if exp > best_train_exp:
            best_train_exp, best_n = exp, n

    print(f"\n=== Chosen N={best_n} (best on TRAIN) -- checking HOLDOUT once ===")
    tri = run_all(bars_by_symbol, donchian_period=best_n)
    tri_train, tri_holdout = split_train_holdout(tri)
    describe("triple TRAIN", tri_train)
    describe("triple HOLDOUT", tri_holdout)

    by_method = {}
    for t in tri:
        by_method.setdefault(t['method'], []).append(t)
    print("\n--- contribution by signal (all trades) ---")
    for m, ts in sorted(by_method.items()):
        describe(f"  {m}", ts)

    print("\n--- Donchian trades on HOLDOUT only ---")
    donch_holdout = [t for t in tri_holdout if t['method'] == 'donchian']
    describe("  donchian HOLDOUT", donch_holdout)

    print("\n=== Verdict (pre-registered criteria) ===")
    uplift = len(tri) / len(base) if base else 0
    crit1 = uplift >= MIN_TRADE_COUNT_UPLIFT
    crit2 = expectancy(tri_holdout) >= expectancy(base_holdout)
    print(f"  1. trade count uplift  : {len(base)} -> {len(tri)} = {uplift:.2f}x "
          f"(need >= {MIN_TRADE_COUNT_UPLIFT:.2f}x)  {'PASS' if crit1 else 'FAIL'}")
    print(f"  2. holdout expectancy  : {expectancy(base_holdout) * 100:+.3f}% -> "
          f"{expectancy(tri_holdout) * 100:+.3f}%  (must not fall)  {'PASS' if crit2 else 'FAIL'}")
    print()
    if crit1 and crit2:
        print("  PASS on both -- Donchian is worth implementing as a third live signal.")
    elif not crit1 and crit2:
        print("  REJECT: expectancy held up but it didn't add enough trades to be worth "
              "the extra moving part -- the entire point was frequency.")
    elif crit1 and not crit2:
        print("  REJECT: it does trade more, but pays for it in expectancy. This is exactly "
              "the trade-off the pre-registered criteria exist to refuse.")
    else:
        print("  REJECT on both counts.")


if __name__ == '__main__':
    main()
