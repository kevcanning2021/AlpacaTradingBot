"""Regression tests for trader.py's dual-signal (Bollinger + EMA) plumbing in
scan_and_execute(): held_methods construction, method persistence on fill,
and -- the key correctness claim of the 2026-08-28 port -- that the existing
Research Agent veto covers a Bollinger-sourced buy exactly as it already
covers an EMA-sourced one, with no second call site needed (unlike
_handle_reentry, fixed earlier that same day, which really was a separate
create_order() call).

Mocked throughout, same conventions as test_scan_and_execute_with_agent.py:
a fake AlpacaClient, TradingManager built via __new__() bypassing __init__,
trader.OpportunityScanner.scan patched at the class level, trader.research_propose
patched by name (see that file's docstring for why the patch target matters
here specifically).

Run with: python -m unittest tests.test_scan_and_execute_dual_signal -v
"""
import unittest
from unittest.mock import MagicMock, patch

import trader
from trader import TradingManager
from config import settings


def make_manager(client, position_methods=None):
    tm = TradingManager.__new__(TradingManager)
    tm.client = client
    tm.notifier = MagicMock(enabled=False)
    tm.position_methods = position_methods if position_methods is not None else {}
    tm.position_peak_prices = {}
    tm.position_opened_at = {}
    tm.reentry_fired = set()
    tm.trade_history = []
    tm.win_streak = 0
    tm.loss_streak = 0
    return tm


BOLLINGER_BUY = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'Bollinger lower-band bounce', 'price': 200.0,
                  'rsi': 35.0, 'method': 'bollinger'}
EMA_BUY = {'symbol': 'MSFT', 'signal': 'buy', 'reason': 'EMA9 crossed above EMA21', 'price': 400.0,
           'rsi': 55.0, 'method': 'ema'}


class HeldMethodsWiringTests(unittest.TestCase):
    def test_held_methods_built_from_position_methods_and_passed_to_scan(self):
        client = MagicMock()
        client.get_positions.return_value = [
            {'symbol': 'AAPL', 'asset_class': 'us_equity', 'current_price': 200.0},
            {'symbol': 'GOOGL', 'asset_class': 'us_equity', 'current_price': 150.0},  # not in position_methods
        ]
        client.get_account.return_value = {'buying_power': '100000'}
        tm = make_manager(client, position_methods={'AAPL': 'bollinger', 'TSLA': 'ema'})  # TSLA not currently held
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[]) as mock_scan:
            tm.scan_and_execute(watchlist=['AAPL', 'GOOGL'], position_size_usd=500, max_positions=5)
        _, kwargs = mock_scan.call_args
        # Only AAPL (held AND tracked) should be present -- GOOGL has no
        # tracked method (pre-dates dual signal / opened flag-off), TSLA
        # isn't currently held at all despite being in position_methods.
        self.assertEqual(kwargs['held_methods'], {'AAPL': 'bollinger'})

    def test_positions_fetch_failure_returns_no_signals(self):
        """Real behavior change from the fetch-order swap: on a positions/
        account fetch failure, held_methods can't be safely computed, so this
        must return zero signals rather than stale pre-fetch ones."""
        client = MagicMock()
        client.get_positions.side_effect = Exception('API down')
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BOLLINGER_BUY]) as mock_scan:
            result = tm.scan_and_execute(watchlist=['AAPL'], position_size_usd=500, max_positions=5)
        mock_scan.assert_not_called()
        self.assertEqual(result['signals'], [])
        self.assertEqual(result['executed'], [])


class MethodPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._orig_veto = settings.RESEARCH_AGENT_VETO_ENABLED
        settings.RESEARCH_AGENT_VETO_ENABLED = False

    def tearDown(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = self._orig_veto

    def test_bollinger_buy_persists_method_on_fill(self):
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-1'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BOLLINGER_BUY]), \
             patch.object(tm, '_save_position_methods') as mock_save:
            tm.scan_and_execute(watchlist=['AAPL'], position_size_usd=500, max_positions=5)
        self.assertEqual(tm.position_methods['AAPL'], 'bollinger')
        mock_save.assert_called_once()

    def test_ema_buy_persists_method_on_fill(self):
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-2'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[EMA_BUY]), \
             patch.object(tm, '_save_position_methods') as mock_save:
            tm.scan_and_execute(watchlist=['MSFT'], position_size_usd=500, max_positions=5)
        self.assertEqual(tm.position_methods['MSFT'], 'ema')
        mock_save.assert_called_once()

    def test_crypto_buy_never_touches_position_methods(self):
        """Crypto signals never carry a 'method' key -- confirms the buy
        branch's `if 'method' in sig` guard correctly no-ops for them."""
        crypto_buy = {'symbol': 'BTC/USD', 'signal': 'buy', 'reason': 'EMA9 crossed above EMA21',
                       'price': 50000.0, 'rsi': 40.0}
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-3'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[crypto_buy]), \
             patch.object(tm, '_save_position_methods') as mock_save:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=5)
        mock_save.assert_not_called()
        self.assertEqual(tm.position_methods, {})

    def test_position_methods_popped_on_stop_loss_close(self):
        position = {'symbol': 'AAPL', 'asset_class': 'us_equity', 'current_price': 190.0,
                    'avg_entry_price': 200.0, 'qty': 2.5}
        client = MagicMock()
        client.close_position.return_value = {}
        tm = make_manager(client, position_methods={'AAPL': 'bollinger'})
        report = {'actions_taken': [], 'errors': []}
        with patch.object(tm, '_record_trade_outcome'), \
             patch.object(tm, '_save_peak_prices'), \
             patch.object(tm, '_save_position_opened_at'), \
             patch.object(tm, '_save_position_methods') as mock_save, \
             patch.object(tm, '_save_reentry_state'):
            # -5% is the default STOP_LOSS_THRESHOLD trigger
            closed = tm._handle_stop_loss('AAPL', position, pnl_pct=-0.06, report=report)
        self.assertTrue(closed)
        self.assertNotIn('AAPL', tm.position_methods)
        mock_save.assert_called_once()

    def test_position_methods_popped_on_trailing_stop_close(self):
        tm_client = MagicMock()
        tm_client.close_position.return_value = {}
        tm = make_manager(tm_client, position_methods={'AAPL': 'ema'})
        tm.position_peak_prices = {'AAPL': 220.0}
        position = {'symbol': 'AAPL', 'asset_class': 'us_equity', 'current_price': 200.0}
        report = {'actions_taken': [], 'errors': []}
        with patch.object(tm, '_record_trade_outcome'), \
             patch.object(tm, '_save_peak_prices'), \
             patch.object(tm, '_save_position_opened_at'), \
             patch.object(tm, '_save_position_methods') as mock_save, \
             patch.object(tm, '_save_reentry_state'):
            # (220-200)/220 = 9.1% pullback, above the default 8% TRAILING_STOP_THRESHOLD
            closed = tm._handle_trailing_stop('AAPL', position, report=report)
        self.assertTrue(closed)
        self.assertNotIn('AAPL', tm.position_methods)
        mock_save.assert_called_once()

    def test_position_methods_popped_on_scanner_sell(self):
        sell_signal = {'symbol': 'AAPL', 'signal': 'sell', 'reason': 'Reverted to middle band', 'price': 210.0}
        client = MagicMock()
        client.get_positions.return_value = [{'symbol': 'AAPL', 'asset_class': 'us_equity', 'current_price': 210.0}]
        client.get_account.return_value = {'buying_power': '100000'}
        client.close_position.return_value = {}
        tm = make_manager(client, position_methods={'AAPL': 'bollinger'})
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[sell_signal]), \
             patch.object(tm, '_record_trade_outcome'), \
             patch.object(tm, '_save_peak_prices'), \
             patch.object(tm, '_save_position_opened_at'), \
             patch.object(tm, '_save_position_methods') as mock_save, \
             patch.object(tm, '_save_reentry_state'):
            tm.scan_and_execute(watchlist=['AAPL'], position_size_usd=500, max_positions=5)
        self.assertNotIn('AAPL', tm.position_methods)
        mock_save.assert_called_once()


class VetoCoversBothMethodsTests(unittest.TestCase):
    """The key correctness claim of this port: a Bollinger-sourced buy flows
    through the exact same veto check as an EMA-sourced one, because both
    just end up as entries in scan_and_execute()'s one signals list -- no
    second call site exists to have missed, unlike _handle_reentry."""

    def setUp(self):
        self._orig_veto = settings.RESEARCH_AGENT_VETO_ENABLED
        settings.RESEARCH_AGENT_VETO_ENABLED = True

    def tearDown(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = self._orig_veto

    def test_veto_blocks_a_bollinger_sourced_buy(self):
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BOLLINGER_BUY]), \
             patch('trader.research_propose', return_value={'veto': True, 'confidence': 0.9,
                                                              'reasoning': 'earnings miss', 'risk_flags': [],
                                                              'failed': False}) as mock_propose:
            result = tm.scan_and_execute(watchlist=['AAPL'], position_size_usd=500, max_positions=5)
        mock_propose.assert_called_once_with(BOLLINGER_BUY)
        client.create_order.assert_not_called()
        self.assertEqual(result['executed'], [])
        self.assertNotIn('AAPL', tm.position_methods)  # never opened, nothing to track

    def test_veto_blocks_an_ema_sourced_buy(self):
        """No-regression check on the pre-existing path."""
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[EMA_BUY]), \
             patch('trader.research_propose', return_value={'veto': True, 'confidence': 0.9,
                                                              'reasoning': 'earnings miss', 'risk_flags': [],
                                                              'failed': False}) as mock_propose:
            result = tm.scan_and_execute(watchlist=['MSFT'], position_size_usd=500, max_positions=5)
        mock_propose.assert_called_once_with(EMA_BUY)
        client.create_order.assert_not_called()
        self.assertEqual(result['executed'], [])

    def test_veto_allows_a_bollinger_sourced_buy_through_when_not_vetoed(self):
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-4'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BOLLINGER_BUY]), \
             patch('trader.research_propose', return_value={'veto': False, 'confidence': 0.7,
                                                              'reasoning': 'no adverse news', 'risk_flags': [],
                                                              'failed': False}), \
             patch.object(tm, '_save_position_methods') as mock_save:
            result = tm.scan_and_execute(watchlist=['AAPL'], position_size_usd=500, max_positions=5)
        self.assertTrue(client.create_order.called)
        self.assertEqual(len(result['executed']), 1)
        self.assertEqual(tm.position_methods['AAPL'], 'bollinger')
        mock_save.assert_called_once()


if __name__ == '__main__':
    unittest.main()
