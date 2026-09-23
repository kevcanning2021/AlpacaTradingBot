"""Momentum/breakout intraday signal -- a deliberately different basis from
intraday_signals.py, which was tested to a decisive negative result.

Why a new signal family at all: the armed-window test (2026-09-16, see
STRATEGY.md) established on 281-296 trades that 15m Bollinger
mean-reversion plus a 5m EMA-cross trigger has NO edge on this watchlist
intraday -- holdout -0.172%/trade at a 37.6% win rate against a 40%
break-even, with 9 of 12 symbols negative. That conclusion explicitly said
any future intraday attempt should use a genuinely different signal basis.
Mean reversion bets that a move exhausts; this bets the opposite, that a
move continues.

Two structural rules carried over from what went wrong before:

1. **State x event, never event x event.** The first two attempts failed
   mechanically, not statistically: they required a two-bar Bollinger
   bounce and a one-bar EMA cross to be true on the SAME 5-minute bar, and
   two rare instantaneous events demanded to coincide is their
   probabilities multiplied (13-20 trades in six months). Here the 1h
   filter is a *state* -- an uptrend is true for long stretches -- and only
   the 5m breakout is an event. A state gates an event without starving it,
   so no arming window is needed to compensate.

2. **Parameters are reasoned, not grid-searched.** BREAKOUT_LOOKBACK_5M is
   fixed at one hour of bars because that is the horizon a discretionary
   trader would call "the recent high", not because it scored best. Only
   stop_pct is selected, and only on TRAIN. LESSONS 17: choosing whichever
   variant looks best on holdout is itself overfitting, even when each
   variant's own split was done correctly.

Exit mechanics are deliberately identical to the failed test (check_exit's
fixed percentage stop and target) so that entry signal is the only variable
that changed between the two runs.
"""
from dataclasses import dataclass
from typing import List, Optional

from scanner import _compute_ema

# One hour of 5-minute bars. The breakout level is the highest close of the
# preceding hour -- long enough to be a level worth breaking, short enough
# that it still breaks often. Reasoned, not fitted.
BREAKOUT_LOOKBACK_5M = 12

# 1h trend state. 20/50 rather than the 9/21 the daily scanner uses: on
# hourly bars 9/21 spans barely two sessions and flips constantly, which
# would make this a fast oscillator rather than the slow regime gate it is
# meant to be.
TREND_EMA_FAST_1H = 20
TREND_EMA_SLOW_1H = 50

MIN_WARMUP_1H = TREND_EMA_SLOW_1H + 5


@dataclass(frozen=True)
class MomentumSignal:
    symbol: str
    price: float
    reason: str


def trend_up_1h(closes_1h: List[float]) -> bool:
    """Regime gate: EMA20 > EMA50 on the most recent closed 1h bar.

    A STATE, true across long stretches -- this is what keeps the
    conjunction from starving (see module docstring). Long-only, matching
    every other strategy in this fleet.
    """
    if len(closes_1h) < MIN_WARMUP_1H:
        return False
    fast = _compute_ema(closes_1h, TREND_EMA_FAST_1H)
    slow = _compute_ema(closes_1h, TREND_EMA_SLOW_1H)
    if not fast or not slow:
        return False
    return fast[-1] > slow[-1]


def breakout_5m(closes_5m: List[float], lookback: int = BREAKOUT_LOOKBACK_5M) -> bool:
    """The event: this bar closed above every close of the preceding
    `lookback` bars. Strictly greater, and the current bar is excluded from
    its own comparison window -- otherwise the test is trivially true.
    """
    if len(closes_5m) < lookback + 1:
        return False
    prior = closes_5m[-(lookback + 1):-1]
    return closes_5m[-1] > max(prior)


def evaluate(symbol: str, closes_5m: List[float], trend_ok: bool) -> Optional[MomentumSignal]:
    """Entry when the 1h regime is up and the 5m bar breaks the recent high.

    trend_ok is passed in rather than computed here so this stays pure over
    lists of floats -- the caller (backtest or live runner) owns fetching
    the 1h window as-of the current bar, which is where lookahead would
    otherwise creep in.
    """
    if not trend_ok:
        return None
    if not breakout_5m(closes_5m):
        return None
    return MomentumSignal(
        symbol=symbol,
        price=closes_5m[-1],
        reason=f"1h uptrend + 5m breakout of {BREAKOUT_LOOKBACK_5M}-bar high",
    )
