"""Regression tests for intraday_signals.py (Phase 1 of the Sofi Intraday
plan) -- pure functions, no network, mocking _compute_ema/_compute_rsi/
_compute_bollinger directly for exact control over the underlying math,
same convention as tests/test_scanner_bollinger.py.

Run with: python -m unittest tests.test_intraday_signals -v
"""
import unittest
from unittest.mock import patch

import intraday_signals as sig


class TrendFilter1hTests(unittest.TestCase):
    def test_uptrend_when_ema9_above_ema21(self):
        with patch('intraday_signals._compute_ema', side_effect=[[105.0], [100.0]]):
            self.assertTrue(sig.trend_filter_1h([100.0] * 25))

    def test_no_uptrend_when_ema9_below_ema21(self):
        with patch('intraday_signals._compute_ema', side_effect=[[95.0], [100.0]]):
            self.assertFalse(sig.trend_filter_1h([100.0] * 25))

    def test_empty_ema_is_not_an_uptrend(self):
        """Too few bars to seed either EMA -- must not raise, must not
        claim an uptrend it can't actually compute."""
        with patch('intraday_signals._compute_ema', side_effect=[[], []]):
            self.assertFalse(sig.trend_filter_1h([100.0, 101.0]))


class Setup15mTests(unittest.TestCase):
    def test_fires_on_lower_band_bounce_with_oversold_rsi(self):
        closes = [110.0] * 18 + [95.0, 102.0]  # prev_price=95, price=102
        lower = [None] * 18 + [100.0, 98.0]
        with patch('intraday_signals._compute_bollinger', return_value=(None, None, lower)), \
             patch('intraday_signals._compute_rsi', return_value=35.0):
            self.assertTrue(sig.setup_15m(closes))

    def test_no_signal_without_a_band_touch(self):
        closes = [110.0] * 18 + [105.0, 106.0]
        lower = [None] * 18 + [100.0, 98.0]
        with patch('intraday_signals._compute_bollinger', return_value=(None, None, lower)), \
             patch('intraday_signals._compute_rsi', return_value=35.0):
            self.assertFalse(sig.setup_15m(closes))

    def test_no_signal_when_rsi_not_oversold(self):
        closes = [110.0] * 18 + [95.0, 102.0]
        lower = [None] * 18 + [100.0, 98.0]
        with patch('intraday_signals._compute_bollinger', return_value=(None, None, lower)), \
             patch('intraday_signals._compute_rsi', return_value=55.0):
            self.assertFalse(sig.setup_15m(closes))

    def test_too_few_bars_is_not_a_signal(self):
        with patch('intraday_signals._compute_bollinger', return_value=(None, None, [None])):
            self.assertFalse(sig.setup_15m([100.0]))


class EntryTrigger5mTests(unittest.TestCase):
    def test_fires_on_ema_cross_upward(self):
        with patch('intraday_signals._compute_ema', side_effect=[[99.0, 101.0], [100.0, 100.0]]):
            self.assertTrue(sig.entry_trigger_5m([100.0] * 25))

    def test_no_signal_without_a_cross(self):
        """EMA9 already above EMA21 on both bars -- no fresh cross, no signal."""
        with patch('intraday_signals._compute_ema', side_effect=[[102.0, 103.0], [100.0, 100.0]]):
            self.assertFalse(sig.entry_trigger_5m([100.0] * 25))

    def test_downward_cross_is_not_a_signal(self):
        with patch('intraday_signals._compute_ema', side_effect=[[101.0, 99.0], [100.0, 100.0]]):
            self.assertFalse(sig.entry_trigger_5m([100.0] * 25))


class EvaluateTests(unittest.TestCase):
    """evaluate() now takes the arming state as a parameter rather than
    re-deriving the 15m setup itself -- the caller owns that state (see
    ARMED_WINDOW_5M_BARS). These tests cover the pure part: given an
    armed/unarmed setup and a 5m trigger, does a signal fire."""

    def test_signal_when_armed_and_5m_trigger_fires(self):
        with patch('intraday_signals.entry_trigger_5m', return_value=True):
            result = sig.evaluate('AAPL', [100.0, 101.0], setup_active=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol, 'AAPL')
        self.assertEqual(result.price, 101.0)

    def test_no_signal_when_not_armed(self):
        """The 5m trigger alone is not enough -- a recent 15m setup must
        have armed the symbol first."""
        with patch('intraday_signals.entry_trigger_5m', return_value=True):
            self.assertIsNone(sig.evaluate('AAPL', [100.0, 101.0], setup_active=False))

    def test_no_signal_when_armed_but_no_5m_trigger(self):
        """Being armed is not itself an entry -- the 5m trigger still has
        to fire within the window."""
        with patch('intraday_signals.entry_trigger_5m', return_value=False):
            self.assertIsNone(sig.evaluate('AAPL', [100.0, 101.0], setup_active=True))

    def test_no_signal_with_no_5m_bars_at_all(self):
        """Guards against calling any sub-function on an empty bar list."""
        self.assertIsNone(sig.evaluate('AAPL', [], setup_active=True))

    def test_setup_15m_is_not_called_by_evaluate(self):
        """Regression guard: arming is the caller's job now. If evaluate()
        ever re-derives the setup itself, the armed window silently stops
        working (it'd collapse back to same-bar coincidence, the exact
        thing that produced the two rejected attempts)."""
        with patch('intraday_signals.setup_15m') as mock_setup, \
             patch('intraday_signals.entry_trigger_5m', return_value=True):
            sig.evaluate('AAPL', [100.0, 101.0], setup_active=True)
        mock_setup.assert_not_called()


class CheckExitTests(unittest.TestCase):
    def test_neither_level_hit_returns_none(self):
        self.assertIsNone(sig.check_exit(entry_price=100.0, current_price=100.2, stop_pct=0.005))

    def test_stop_hit_returns_stop(self):
        # stop_pct=0.005 -> stop at 99.50
        self.assertEqual(sig.check_exit(entry_price=100.0, current_price=99.0, stop_pct=0.005), 'stop')

    def test_target_hit_returns_target(self):
        # stop_pct=0.005, target_r=1.5 -> target at 100.75
        self.assertEqual(sig.check_exit(entry_price=100.0, current_price=101.0, stop_pct=0.005, target_r=1.5), 'target')

    def test_exactly_at_stop_price_counts_as_stop(self):
        """Boundary: <= stop_price, not strictly less than -- must not let a
        trade sit at exactly the stop level and never close."""
        self.assertEqual(sig.check_exit(entry_price=100.0, current_price=99.5, stop_pct=0.005), 'stop')

    def test_exactly_at_target_price_counts_as_target(self):
        self.assertEqual(sig.check_exit(entry_price=100.0, current_price=100.75, stop_pct=0.005, target_r=1.5), 'target')


if __name__ == '__main__':
    unittest.main()
