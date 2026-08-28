"""Regression tests for the dual-signal (Bollinger + EMA9/21) stock entry
logic added to scanner.py 2026-08-28 -- see BOT_REGISTRY.md / the approved
plan for the full backtest rationale.

Signal-branching tests (_analyze_bars routing/tie-break) mock
_analyze_bollinger/_analyze_ema_crossover directly rather than hand-crafting
real price series that happen to trigger specific conditions -- both already
take rsi as a plain parameter (not computed internally), so mocking the two
analyzer methods isolates _analyze_bars's routing logic from their internal
math entirely. The _analyze_bollinger condition tests below mock
_compute_bollinger the same way, for the same reason.

Run with: python -m unittest tests.test_scanner_bollinger -v
"""
import unittest
from unittest.mock import patch

from scanner import OpportunityScanner
from config import settings


def _bars(n=25):
    return [{'c': 100.0} for _ in range(n)]


class BollingerConditionTests(unittest.TestCase):
    """_analyze_bollinger in isolation, mocking _compute_bollinger so the
    band values are exact and explicit rather than derived from real data."""

    def setUp(self):
        self.scanner = OpportunityScanner(client=None)

    def test_buy_fires_on_lower_band_bounce_with_oversold_rsi(self):
        closes = [110.0] * 18 + [95.0, 102.0]  # prev_price=95, price=102
        mid = [None] * 18 + [110.0, 110.0]
        upper = [None] * 18 + [120.0, 120.0]
        lower = [None] * 18 + [100.0, 98.0]
        with patch('scanner._compute_bollinger', return_value=(mid, upper, lower)):
            result = self.scanner._analyze_bollinger('AAPL', closes, price=102.0, rsi=35.0)
        self.assertEqual(result['signal'], 'buy')
        self.assertIn('Bollinger lower-band bounce', result['reason'])

    def test_no_buy_without_a_band_touch(self):
        """prev_price stays above lower[-2] -- no touch, no buy, even with
        oversold RSI."""
        closes = [110.0] * 18 + [105.0, 106.0]
        mid = [None] * 18 + [110.0, 110.0]
        upper = [None] * 18 + [120.0, 120.0]
        lower = [None] * 18 + [100.0, 98.0]
        with patch('scanner._compute_bollinger', return_value=(mid, upper, lower)):
            result = self.scanner._analyze_bollinger('AAPL', closes, price=106.0, rsi=35.0)
        self.assertNotEqual(result['signal'], 'buy')

    def test_no_buy_when_rsi_not_oversold(self):
        """A real band touch-and-bounce, but RSI >= BOLLINGER_OVERSOLD_RSI (40)
        -- momentum hasn't confirmed, no buy."""
        closes = [110.0] * 18 + [95.0, 102.0]
        mid = [None] * 18 + [110.0, 110.0]
        upper = [None] * 18 + [120.0, 120.0]
        lower = [None] * 18 + [100.0, 98.0]
        with patch('scanner._compute_bollinger', return_value=(mid, upper, lower)):
            result = self.scanner._analyze_bollinger('AAPL', closes, price=102.0, rsi=45.0)
        self.assertNotEqual(result['signal'], 'buy')

    def test_sell_on_mid_band_reversion(self):
        closes = [95.0] * 20
        mid = [None] * 18 + [100.0, 100.0]
        upper = [None] * 18 + [110.0, 110.0]
        lower = [None] * 18 + [90.0, 90.0]
        with patch('scanner._compute_bollinger', return_value=(mid, upper, lower)):
            result = self.scanner._analyze_bollinger('AAPL', closes, price=101.0, rsi=55.0)
        self.assertEqual(result['signal'], 'sell')
        self.assertIn('Reverted to middle band', result['reason'])

    def test_sell_on_overbought_rsi(self):
        closes = [95.0] * 20
        mid = [None] * 18 + [100.0, 100.0]
        upper = [None] * 18 + [110.0, 110.0]
        lower = [None] * 18 + [90.0, 90.0]
        with patch('scanner._compute_bollinger', return_value=(mid, upper, lower)):
            result = self.scanner._analyze_bollinger('AAPL', closes, price=95.0, rsi=85.0)
        self.assertEqual(result['signal'], 'sell')
        self.assertIn('Overbought', result['reason'])


class AnalyzeBarsRoutingTests(unittest.TestCase):
    """_analyze_bars's dual-signal dispatch logic, mocking both analyzer
    methods so only the routing/tie-break behavior is under test."""

    def setUp(self):
        self.scanner = OpportunityScanner(client=None)
        self._orig_flag = settings.DUAL_SIGNAL_BOLLINGER_ENABLED
        settings.DUAL_SIGNAL_BOLLINGER_ENABLED = True

    def tearDown(self):
        settings.DUAL_SIGNAL_BOLLINGER_ENABLED = self._orig_flag

    def test_unheld_symbol_bollinger_wins_on_same_day_tie(self):
        bollinger_buy = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'bb', 'price': 100.0, 'rsi': 35.0}
        ema_buy = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'ema', 'price': 100.0, 'rsi': 35.0}
        with patch.object(self.scanner, '_analyze_bollinger', return_value=dict(bollinger_buy)) as mock_bb, \
             patch.object(self.scanner, '_analyze_ema_crossover', return_value=dict(ema_buy)) as mock_ema:
            result = self.scanner._analyze_bars('AAPL', _bars(), held_method=None)
        mock_bb.assert_called_once()
        mock_ema.assert_not_called()  # Bollinger fired first, EMA never even checked
        self.assertEqual(result['method'], 'bollinger')

    def test_unheld_symbol_ema_fires_when_bollinger_does_not(self):
        bollinger_hold = {'symbol': 'AAPL', 'signal': 'hold', 'reason': 'no signal', 'price': 100.0, 'rsi': 50.0}
        ema_buy = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'ema', 'price': 100.0, 'rsi': 50.0}
        with patch.object(self.scanner, '_analyze_bollinger', return_value=dict(bollinger_hold)), \
             patch.object(self.scanner, '_analyze_ema_crossover', return_value=dict(ema_buy)):
            result = self.scanner._analyze_bars('AAPL', _bars(), held_method=None)
        self.assertEqual(result['method'], 'ema')

    def test_unheld_symbol_neither_fires_returns_bollinger_result(self):
        """Fallback: when neither fires, the Bollinger (hold/sell) result is
        returned -- matches origin/master's behavior exactly, and means the
        'method' key is correctly absent (no position was opened)."""
        bollinger_hold = {'symbol': 'AAPL', 'signal': 'hold', 'reason': 'no signal', 'price': 100.0, 'rsi': 50.0}
        ema_hold = {'symbol': 'AAPL', 'signal': 'hold', 'reason': 'no crossover', 'price': 100.0, 'rsi': 50.0}
        with patch.object(self.scanner, '_analyze_bollinger', return_value=dict(bollinger_hold)), \
             patch.object(self.scanner, '_analyze_ema_crossover', return_value=dict(ema_hold)):
            result = self.scanner._analyze_bars('AAPL', _bars(), held_method=None)
        self.assertNotIn('method', result)
        self.assertEqual(result['reason'], 'no signal')

    def test_held_by_ema_only_checks_ema_even_if_bollinger_would_fire(self):
        """A position already open under the EMA method must only ever be
        re-evaluated against EMA's own exit rule -- never a mixed check,
        even if Bollinger's condition also happens to be true this bar."""
        bollinger_buy = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'bb', 'price': 100.0, 'rsi': 35.0}
        ema_sell = {'symbol': 'AAPL', 'signal': 'sell', 'reason': 'crossunder', 'price': 100.0, 'rsi': 35.0}
        with patch.object(self.scanner, '_analyze_bollinger', return_value=dict(bollinger_buy)) as mock_bb, \
             patch.object(self.scanner, '_analyze_ema_crossover', return_value=dict(ema_sell)) as mock_ema:
            result = self.scanner._analyze_bars('AAPL', _bars(), held_method='ema')
        mock_bb.assert_not_called()
        mock_ema.assert_called_once()
        self.assertEqual(result['signal'], 'sell')

    def test_held_by_bollinger_only_checks_bollinger(self):
        bollinger_sell = {'symbol': 'AAPL', 'signal': 'sell', 'reason': 'reverted', 'price': 100.0, 'rsi': 55.0}
        ema_buy = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'ema', 'price': 100.0, 'rsi': 55.0}
        with patch.object(self.scanner, '_analyze_bollinger', return_value=dict(bollinger_sell)) as mock_bb, \
             patch.object(self.scanner, '_analyze_ema_crossover', return_value=dict(ema_buy)) as mock_ema:
            result = self.scanner._analyze_bars('AAPL', _bars(), held_method='bollinger')
        mock_ema.assert_not_called()
        mock_bb.assert_called_once()
        self.assertEqual(result['signal'], 'sell')

    def test_crypto_always_uses_ema_regardless_of_flag_or_held_method(self):
        """Permanent invariant, not just an oversight-proof: crypto never
        routes through the dual-signal/Bollinger logic at all, no matter
        what the flag or held_method say."""
        ema_result = {'symbol': 'BTC/USD', 'signal': 'hold', 'reason': 'no crossover', 'price': 50000.0, 'rsi': 50.0}
        with patch.object(self.scanner, '_analyze_bollinger') as mock_bb, \
             patch.object(self.scanner, '_analyze_ema_crossover', return_value=dict(ema_result)) as mock_ema:
            self.scanner._analyze_bars('BTC/USD', _bars(), held_method='bollinger')
        mock_bb.assert_not_called()
        mock_ema.assert_called_once()

    def test_flag_off_stock_uses_ema_only_regardless_of_held_method(self):
        """Byte-identical-to-pre-dual-signal behavior when the flag is off
        (the default) -- Bollinger is never even checked."""
        settings.DUAL_SIGNAL_BOLLINGER_ENABLED = False
        ema_result = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'ema', 'price': 100.0, 'rsi': 50.0}
        with patch.object(self.scanner, '_analyze_bollinger') as mock_bb, \
             patch.object(self.scanner, '_analyze_ema_crossover', return_value=dict(ema_result)) as mock_ema:
            result = self.scanner._analyze_bars('AAPL', _bars(), held_method=None)
        mock_bb.assert_not_called()
        mock_ema.assert_called_once()
        self.assertNotIn('method', result)  # flag-off path never tags a method


if __name__ == '__main__':
    unittest.main()
