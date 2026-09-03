"""Regression tests for trader.py: TradingManager.check_positions() resilience,
added 2026-09-03 after a full fleet review found two related gaps: (1) a
get_account() failure aborted stop-loss/trailing-stop checks for every
position that cycle, even though positions were already fetched successfully;
(2) one malformed position (e.g. a halted symbol with a null price field)
raised out of the per-position loop and aborted protection for every other
open position that same cycle. Both are now isolated: account-fetch failures
only skip re-entry sizing (buying_power stays None), and each position is
processed inside its own try/except.

Same TradingManager.__new__() + manual-attribute construction pattern as
test_scan_and_execute_with_agent.py -- real __init__ touches live state
files and a real AlpacaClient, neither of which a unit test may do.

Run with: python -m unittest tests.test_check_positions_resilience -v
"""
import unittest
from unittest.mock import MagicMock

from trader import TradingManager
from config import settings


def make_manager(client):
    tm = TradingManager.__new__(TradingManager)
    tm.client = client
    tm.notifier = MagicMock(enabled=False)
    tm.position_methods = {}
    tm.position_peak_prices = {}
    tm.position_opened_at = {}
    tm.reentry_fired = set()
    tm.trade_history = []
    tm.win_streak = 0
    tm.loss_streak = 0
    # Real _handle_* methods are left in place (this file tests the loop
    # around them); only the file-writing helpers are stubbed so nothing
    # touches real state files on disk.
    tm._save_position_opened_at = MagicMock()
    tm._save_peak_prices = MagicMock()
    tm._save_reentry_state = MagicMock()
    return tm


def make_position(symbol='AAPL', qty='10', current_price='100', avg_entry_price='100'):
    return {'symbol': symbol, 'qty': qty, 'current_price': current_price, 'avg_entry_price': avg_entry_price,
            'asset_class': 'us_equity'}


class AccountFetchFailureTests(unittest.TestCase):
    def setUp(self):
        self._orig = settings.ENABLE_REENTRY
        settings.ENABLE_REENTRY = True

    def tearDown(self):
        settings.ENABLE_REENTRY = self._orig

    def test_account_failure_does_not_skip_stop_loss_and_trailing_stop_checks(self):
        client = MagicMock()
        client.get_positions.return_value = [make_position('AAPL')]
        client.get_account.side_effect = RuntimeError('Alpaca /account timed out')
        tm = make_manager(client)
        tm._handle_stop_loss = MagicMock(return_value=False)
        tm._handle_trailing_stop = MagicMock(return_value=False)
        tm._handle_reentry = MagicMock()

        report = tm.check_positions()

        tm._handle_stop_loss.assert_called_once()
        tm._handle_trailing_stop.assert_called_once()
        self.assertIn('account fetch failed', report['errors'][0])
        self.assertIsNone(report['buying_power'])

    def test_account_failure_skips_reentry_instead_of_sizing_against_none(self):
        client = MagicMock()
        client.get_positions.return_value = [make_position('AAPL')]
        client.get_account.side_effect = RuntimeError('Alpaca /account timed out')
        tm = make_manager(client)
        tm._handle_stop_loss = MagicMock(return_value=False)
        tm._handle_trailing_stop = MagicMock(return_value=False)
        tm._handle_reentry = MagicMock()

        tm.check_positions()

        tm._handle_reentry.assert_not_called()

    def test_successful_account_fetch_still_runs_reentry_as_before(self):
        client = MagicMock()
        client.get_positions.return_value = [make_position('AAPL')]
        client.get_account.return_value = {'buying_power': '1000', 'equity': '5000'}
        tm = make_manager(client)
        tm._handle_stop_loss = MagicMock(return_value=False)
        tm._handle_trailing_stop = MagicMock(return_value=False)
        tm._handle_reentry = MagicMock(return_value=1000.0)

        report = tm.check_positions()

        tm._handle_reentry.assert_called_once()
        self.assertEqual(report['buying_power'], '1000')
        self.assertEqual(report['errors'], [])


class OnePositionFailureTests(unittest.TestCase):
    def test_one_malformed_position_does_not_abort_the_others(self):
        client = MagicMock()
        bad_position = make_position('BADCO', current_price=None)  # float(None) raises TypeError
        good_position = make_position('AAPL')
        client.get_positions.return_value = [bad_position, good_position]
        client.get_account.return_value = {'buying_power': '1000', 'equity': '5000'}
        tm = make_manager(client)
        tm._handle_stop_loss = MagicMock(return_value=False)
        tm._handle_trailing_stop = MagicMock(return_value=False)
        tm._handle_reentry = MagicMock(return_value=1000.0)

        report = tm.check_positions()

        # The good position downstream of the bad one must still be checked --
        # this is the core guarantee: one bad symbol must not cost every other
        # open position its protection for the cycle.
        tm._handle_stop_loss.assert_called_once()
        called_symbol = tm._handle_stop_loss.call_args[0][0]
        self.assertEqual(called_symbol, 'AAPL')
        self.assertEqual(len(report['errors']), 1)
        self.assertIn('BADCO', report['errors'][0])

    def test_error_isolation_works_even_when_both_positions_are_bad(self):
        client = MagicMock()
        client.get_positions.return_value = [make_position('BAD1', current_price=None),
                                               make_position('BAD2', avg_entry_price='not-a-number')]
        client.get_account.return_value = {'buying_power': '1000', 'equity': '5000'}
        tm = make_manager(client)
        tm._handle_stop_loss = MagicMock(return_value=False)

        report = tm.check_positions()

        self.assertEqual(len(report['errors']), 2)
        tm._handle_stop_loss.assert_not_called()


if __name__ == '__main__':
    unittest.main()
