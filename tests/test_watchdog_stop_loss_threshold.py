"""Regression test for watchdog.py: check_account()'s stop-loss-breach check,
added 2026-09-03 after a full fleet review found it used the flat stock
threshold (STOP_LOSS_THRESHOLD, 5%) for every position regardless of asset
class, false-alarming on any crypto position between 5% and CRYPTO_STOP_LOSS_
THRESHOLD (15%) down -- both of which are normal, already-tolerated levels
per trader.py's own _handle_stop_loss, not a real breach.

Mocked throughout: AlpacaClient is patched at the module level (watchdog.py
does `from alpaca_client import AlpacaClient` then constructs one directly
inside check_account(), so no real network call is possible even without the
patch, but this keeps it explicit and fast).

Run with: python -m unittest tests.test_watchdog_stop_loss_threshold -v
"""
import unittest
from unittest.mock import MagicMock, patch

import watchdog


def make_position(symbol, pnl_pct, asset_class):
    return {'symbol': symbol, 'unrealized_plpc': str(pnl_pct), 'asset_class': asset_class}


class StopLossThresholdTests(unittest.TestCase):
    @patch('watchdog.AlpacaClient')
    def test_crypto_at_minus_8_percent_is_not_flagged(self, MockClient):
        """-8% is within crypto's real 15% tolerance -- must not be reported."""
        client = MockClient.return_value
        client.get_positions.return_value = [make_position('BTC/USD', -0.08, 'crypto')]
        client.get_orders.return_value = []
        issues, _ = watchdog.check_account('prod', 'Prod', 'key', 'secret', set())
        self.assertEqual([i for i in issues if 'stop_loss_breach' in i[0]], [])

    @patch('watchdog.AlpacaClient')
    def test_crypto_past_15_percent_is_flagged_with_the_crypto_threshold(self, MockClient):
        client = MockClient.return_value
        client.get_positions.return_value = [make_position('BTC/USD', -0.20, 'crypto')]
        client.get_orders.return_value = []
        issues, _ = watchdog.check_account('prod', 'Prod', 'key', 'secret', set())
        breaches = [i for i in issues if 'stop_loss_breach' in i[0]]
        self.assertEqual(len(breaches), 1)
        self.assertIn('15%', breaches[0][1])

    @patch('watchdog.AlpacaClient')
    def test_stock_at_minus_8_percent_is_still_flagged_with_the_stock_threshold(self, MockClient):
        """Stocks keep the original, tighter 5% threshold -- this must not regress."""
        client = MockClient.return_value
        client.get_positions.return_value = [make_position('AAPL', -0.08, 'us_equity')]
        client.get_orders.return_value = []
        issues, _ = watchdog.check_account('prod', 'Prod', 'key', 'secret', set())
        breaches = [i for i in issues if 'stop_loss_breach' in i[0]]
        self.assertEqual(len(breaches), 1)
        self.assertIn('5%', breaches[0][1])

    @patch('watchdog.AlpacaClient')
    def test_stock_at_minus_3_percent_is_not_flagged(self, MockClient):
        client = MockClient.return_value
        client.get_positions.return_value = [make_position('AAPL', -0.03, 'us_equity')]
        client.get_orders.return_value = []
        issues, _ = watchdog.check_account('prod', 'Prod', 'key', 'secret', set())
        self.assertEqual([i for i in issues if 'stop_loss_breach' in i[0]], [])


if __name__ == '__main__':
    unittest.main()
