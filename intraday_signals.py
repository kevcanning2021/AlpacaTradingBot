"""Pure multi-timeframe signal logic for the Sofi Intraday strategy (Phase 1
of the approved plan) -- a 15m Bollinger setup and a 5m EMA entry trigger
(originally a 3-timeframe conjunction including a 1h trend filter; dropped
2026-09-16 after backtesting showed the 3-way conjunction was too
restrictive to produce a usable sample -- see evaluate()'s own docstring
and STRATEGY.md). No I/O, no ordering, no state: just functions over lists
of floats, so they can be unit-tested without any network/mock scaffolding
and reused identically by both the backtest (Phase 2) and the live session
runner (Phase 3) -- the same shape run against historical vs. live bars
should behave identically, which only holds if the signal logic itself
never touches the network or the clock.

Reuses _compute_ema/_compute_rsi/_compute_bollinger from scanner.py
directly -- confirmed pure list-of-floats math with zero calendar/timeframe
assumptions, so the same functions that already validate to a real,
positive backtest on daily bars are safe to reuse here unchanged. What's
new is only how they're combined across three timeframes.

check_exit()'s stop_pct is deliberately a parameter, not a hardcoded
constant -- Phase 2's backtest selects its value from TRAIN data only (a
small candidate set, exactly one chosen configuration then checked against
HOLDOUT once), never guessed and never picked by whichever candidate looks
best on holdout (see LESSONS.md entry 17: that's itself overfitting). Once
chosen, the SAME check_exit() call (with the winning stop_pct) is reused
unchanged by the live session runner, so backtest and live can't drift
apart on exit logic.
"""
from dataclasses import dataclass
from typing import List, Optional

from scanner import _compute_ema, _compute_rsi, _compute_bollinger

# Same constants as scanner.py's daily-bar dual-signal logic
# (BOLLINGER_PERIOD/BOLLINGER_STD/BOLLINGER_OVERSOLD_RSI) -- reused as-is
# rather than re-guessing new values on a faster timeframe; Phase 2's
# backtest is what actually validates (or rejects) carrying these over.
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
BOLLINGER_OVERSOLD_RSI = 40

# How long a 15m setup stays "armed" for a 5m entry trigger, in 5-minute
# bars. 12 = one hour, chosen on reasoning alone (roughly how long a 15m
# mean-reversion setup stays relevant -- 4 fifteen-minute bars) and
# deliberately NOT grid-searched against backtest results: tuning this
# against the same holdout the rule is judged on is the overfitting trap
# LESSONS.md entry 17 describes. If it ever is tuned, it needs its own
# train-only selection like stop_pct gets.
ARMED_WINDOW_5M_BARS = 12


@dataclass(frozen=True)
class IntradaySignal:
    symbol: str
    price: float
    reason: str


def trend_filter_1h(closes_1h: List[float]) -> bool:
    """Long-only regime gate: EMA9 > EMA21 on the most recent closed 1h bar.
    Matches every other strategy in this fleet (long-only) -- checked here
    as a filter, not an entry trigger in its own right."""
    ema9 = _compute_ema(closes_1h, 9)
    ema21 = _compute_ema(closes_1h, 21)
    if not ema9 or not ema21:
        return False
    return ema9[-1] > ema21[-1]


def setup_15m(closes_15m: List[float]) -> bool:
    """The 'setup': a Bollinger(20,2) lower-band touch-and-bounce with RSI
    confirming oversold, on 15-minute bars -- identical logic to scanner.py's
    already-validated daily-bar _analyze_bollinger, just faster."""
    if len(closes_15m) < 2:
        return False
    _, _, lower = _compute_bollinger(closes_15m, BOLLINGER_PERIOD, BOLLINGER_STD)
    if len(lower) < 2 or lower[-1] is None or lower[-2] is None:
        return False
    rsi = _compute_rsi(closes_15m)
    prev_price, price = closes_15m[-2], closes_15m[-1]
    return prev_price < lower[-2] and price > lower[-1] and rsi < BOLLINGER_OVERSOLD_RSI


def entry_trigger_5m(closes_5m: List[float]) -> bool:
    """The entry timing trigger: EMA9 crosses back above EMA21 on the most
    recently closed 5-minute bar -- momentum confirmation right now, rather
    than entering the instant the (slower) 15m setup condition is first true."""
    ema9 = _compute_ema(closes_5m, 9)
    ema21 = _compute_ema(closes_5m, 21)
    if len(ema9) < 2 or len(ema21) < 2:
        return False
    prev_diff = ema9[-2] - ema21[-2]
    curr_diff = ema9[-1] - ema21[-1]
    return prev_diff < 0 and curr_diff > 0


def evaluate(symbol: str, closes_5m: List[float], setup_active: bool) -> Optional[IntradaySignal]:
    """A real entry when the 5m trigger fires WHILE a recent 15m setup is
    still armed. `setup_active` is supplied by the caller, which owns the
    arming state (see ARMED_WINDOW_5M_BARS) -- this function stays pure.

    Why the caller owns arming rather than this function re-deriving it:
    the two earlier versions (3-timeframe, then 2-timeframe) both required
    the 15m setup and the 5m crossover to be true on the SAME 5-minute
    bar. Both are discrete, ~single-bar events, so demanding they coincide
    exactly is multiplicatively rare -- that mechanism, not any threshold,
    is what produced only 13-20 trades over ~6 months in both rejected
    attempts (see STRATEGY.md). A real trader watching a 15m setup form
    then waiting for a 5m entry over the following bars is the behaviour
    this models, and it needs state across bars, which a pure signal
    function shouldn't hold."""
    if not closes_5m:
        return None
    if not setup_active:
        return None
    if not entry_trigger_5m(closes_5m):
        return None
    return IntradaySignal(
        symbol=symbol, price=closes_5m[-1],
        reason="5m EMA9/21 cross within an armed 15m Bollinger setup",
    )


def check_exit(entry_price: float, current_price: float, stop_pct: float, target_r: float = 1.5) -> Optional[str]:
    """Fixed percentage stop/target relative to entry -- returns 'stop',
    'target', or None if neither level has been hit yet. target_r is the
    reward:risk ratio applied to stop_pct (e.g. stop_pct=0.005, target_r=1.5
    means a stop 0.5% below entry and a target 0.75% above). Deliberately
    simple (no ATR/volatility scaling) for this strategy's first pass --
    stop_pct itself is what Phase 2's backtest selects from data."""
    stop_price = entry_price * (1 - stop_pct)
    target_price = entry_price * (1 + stop_pct * target_r)
    if current_price <= stop_price:
        return 'stop'
    if current_price >= target_price:
        return 'target'
    return None
