"""Tests for intraday_momentum.py -- the momentum/breakout intraday signal.

Written alongside the signal, before its backtest verdict was known, so
these pin the intended mechanics rather than being reverse-engineered from
whatever the numbers turned out to be.

Run with: python -m unittest tests.test_intraday_momentum -v
"""
import unittest

from intraday_momentum import (BREAKOUT_LOOKBACK_5M, MIN_WARMUP_1H, breakout_5m,
                                evaluate, trend_up_1h)


def _rising(n, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


def _falling(n, start=100.0, step=0.5):
    return [start - i * step for i in range(n)]


class BreakoutTests(unittest.TestCase):
    def test_close_above_the_prior_window_high_is_a_breakout(self):
        closes = [10.0] * BREAKOUT_LOOKBACK_5M + [11.0]
        self.assertTrue(breakout_5m(closes))

    def test_close_equal_to_the_prior_high_is_not_a_breakout(self):
        """Strictly greater. A flat series must not read as breaking out of
        itself, or a dead symbol signals continuously."""
        closes = [10.0] * BREAKOUT_LOOKBACK_5M + [10.0]
        self.assertFalse(breakout_5m(closes))

    def test_current_bar_is_excluded_from_its_own_comparison_window(self):
        """If the current close were included in the max() it compares
        against, the test would be trivially false for every bar -- the
        mirror of the off-by-one that would make it trivially true."""
        closes = _rising(BREAKOUT_LOOKBACK_5M + 1)
        self.assertTrue(breakout_5m(closes))  # each bar exceeds all prior

    def test_too_few_bars_is_not_a_breakout(self):
        self.assertFalse(breakout_5m([1.0, 2.0, 3.0]))

    def test_a_high_earlier_in_the_window_blocks_the_breakout(self):
        """The level to beat is the window's max, not merely the previous
        bar -- a pullback that ticks up must not count as a new high."""
        closes = [10.0, 20.0] + [11.0] * (BREAKOUT_LOOKBACK_5M - 2) + [12.0]
        self.assertFalse(breakout_5m(closes))


class TrendFilterTests(unittest.TestCase):
    def test_rising_series_is_an_uptrend(self):
        self.assertTrue(trend_up_1h(_rising(MIN_WARMUP_1H + 20)))

    def test_falling_series_is_not(self):
        self.assertFalse(trend_up_1h(_falling(MIN_WARMUP_1H + 20)))

    def test_insufficient_history_is_not_an_uptrend(self):
        """Fails closed: too little history to judge the regime must mean
        no trade, not an assumed uptrend."""
        self.assertFalse(trend_up_1h(_rising(MIN_WARMUP_1H - 1)))

    def test_trend_is_a_state_not_an_event(self):
        """The structural property the whole design rests on: an uptrend
        must hold across many consecutive bars. If it only flickered true
        occasionally, gating a rare breakout event with it would starve the
        conjunction -- exactly how the previous intraday attempt produced
        13 trades in six months."""
        closes = _rising(MIN_WARMUP_1H + 40)
        true_count = sum(trend_up_1h(closes[:i]) for i in range(MIN_WARMUP_1H, len(closes)))
        self.assertGreater(true_count, 30, 'uptrend should persist, not flicker')


class EvaluateTests(unittest.TestCase):
    def test_signal_requires_both_trend_and_breakout(self):
        breaking = [10.0] * BREAKOUT_LOOKBACK_5M + [11.0]
        self.assertIsNotNone(evaluate('AAPL', breaking, trend_ok=True))
        self.assertIsNone(evaluate('AAPL', breaking, trend_ok=False))

    def test_no_signal_without_a_breakout_even_in_an_uptrend(self):
        flat = [10.0] * (BREAKOUT_LOOKBACK_5M + 1)
        self.assertIsNone(evaluate('AAPL', flat, trend_ok=True))

    def test_signal_carries_price_and_reason(self):
        breaking = [10.0] * BREAKOUT_LOOKBACK_5M + [11.0]
        sig = evaluate('AAPL', breaking, trend_ok=True)
        self.assertEqual(sig.symbol, 'AAPL')
        self.assertEqual(sig.price, 11.0)
        self.assertIn('breakout', sig.reason)


if __name__ == '__main__':
    unittest.main()
