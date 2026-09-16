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
    """evaluate() combines all three -- mock each sub-function directly
    rather than re-deriving real price series for every combination."""

    def test_signal_only_when_all_three_align(self):
        with patch('intraday_signals.trend_filter_1h', return_value=True), \
             patch('intraday_signals.setup_15m', return_value=True), \
             patch('intraday_signals.entry_trigger_5m', return_value=True):
            result = sig.evaluate('AAPL', [1.0], [1.0], [100.0, 101.0])
        self.assertIsNotNone(result)
        self.assertEqual(result.symbol, 'AAPL')
        self.assertEqual(result.price, 101.0)

    def test_no_signal_when_1h_trend_fails(self):
        with patch('intraday_signals.trend_filter_1h', return_value=False), \
             patch('intraday_signals.setup_15m', return_value=True), \
             patch('intraday_signals.entry_trigger_5m', return_value=True):
            self.assertIsNone(sig.evaluate('AAPL', [1.0], [1.0], [100.0, 101.0]))

    def test_no_signal_when_15m_setup_fails(self):
        with patch('intraday_signals.trend_filter_1h', return_value=True), \
             patch('intraday_signals.setup_15m', return_value=False), \
             patch('intraday_signals.entry_trigger_5m', return_value=True):
            self.assertIsNone(sig.evaluate('AAPL', [1.0], [1.0], [100.0, 101.0]))

    def test_no_signal_when_5m_trigger_fails(self):
        with patch('intraday_signals.trend_filter_1h', return_value=True), \
             patch('intraday_signals.setup_15m', return_value=True), \
             patch('intraday_signals.entry_trigger_5m', return_value=False):
            self.assertIsNone(sig.evaluate('AAPL', [1.0], [1.0], [100.0, 101.0]))

    def test_no_signal_with_no_5m_bars_at_all(self):
        """Guards against calling any sub-function on an empty bar list."""
        self.assertIsNone(sig.evaluate('AAPL', [1.0], [1.0], []))


if __name__ == '__main__':
    unittest.main()
