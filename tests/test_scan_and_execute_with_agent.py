"""Regression tests for the research-agent veto wiring in trader.py:
scan_and_execute(). Ported from origin/master's test of the same name
2026-08-28, with one required fix: that version mocks
agents.research_agent.propose, which does NOT work here -- Main's trader.py
imports the function by value at load time (`from agents.research_agent
import propose as research_propose`), so the mock must target
`trader.research_propose` instead, or it silently never fires and the test
would attempt (or fail attempting) a real Anthropic call rather than testing
anything. Mocked throughout: a fake AlpacaClient (never a real one, per
LESSONS.md #7 -- "unit testing a method with live side effects against the
real client is not a dry run") and trader.research_propose (no real
Anthropic calls).

TradingManager is built via __new__() + manual attribute assignment rather
than TradingManager(), deliberately bypassing __init__ -- a real
construction calls AlpacaClient()/TelegramNotifier() and loads/can write real
state files from the repo root (peak_prices_state.json etc.), which a unit
test must never touch. Test signals are all crypto (BTC/USD): scanner.py
never sets a 'method' tag on crypto signals, so a crypto buy's happy path
never touches position_methods -- no save-method mocking needed for those
cases either.

Run with: python -m unittest tests.test_scan_and_execute_with_agent -v
"""
import unittest
from unittest.mock import MagicMock, patch

import trader
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
    return tm


BUY_SIGNAL = {'symbol': 'BTC/USD', 'signal': 'buy', 'reason': 'Donchian breakout', 'price': 78000.0, 'rsi': 87.9}


class ScanAndExecuteAgentWiringTests(unittest.TestCase):
    def setUp(self):
        self._orig_veto = settings.RESEARCH_AGENT_VETO_ENABLED
        # This whole file tests against BTC/USD -- must not depend on whatever
        # CRYPTO_TRADING_ENABLED happens to be in the real .env (paused
        # 2026-09-01 after a negative backtest) or every buy here gets
        # silently skipped by that gate before ever reaching research_propose,
        # which is what these tests exist to verify. Found 2026-09-02 when the
        # full suite was run for the first time since that env change.
        self._orig_crypto_enabled = settings.CRYPTO_TRADING_ENABLED
        settings.CRYPTO_TRADING_ENABLED = True

    def tearDown(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = self._orig_veto
        settings.CRYPTO_TRADING_ENABLED = self._orig_crypto_enabled

    def test_veto_disabled_by_default_agent_never_called(self):
        """Default config (RESEARCH_AGENT_VETO_ENABLED=False) must place the
        order exactly as before this feature existed -- no behavior change,
        no import of agents.research_agent at all."""
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-123'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]), \
             patch('trader.research_propose') as mock_propose:
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        mock_propose.assert_not_called()
        self.assertTrue(client.create_order.called)
        self.assertEqual(len(result['executed']), 1)

    def test_veto_enabled_and_vetoed_blocks_the_buy(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]), \
             patch('trader.research_propose', return_value={'veto': True, 'confidence': 0.9, 'reasoning': 'earnings miss', 'risk_flags': [], 'failed': False}) as mock_propose:
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        mock_propose.assert_called_once_with(BUY_SIGNAL, client=client)
        client.create_order.assert_not_called()
        self.assertEqual(result['executed'], [])

    def test_veto_enabled_and_not_vetoed_allows_the_buy(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-456'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]), \
             patch('trader.research_propose', return_value={'veto': False, 'confidence': 0.7, 'reasoning': 'no adverse news', 'risk_flags': [], 'failed': False}) as mock_propose:
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        mock_propose.assert_called_once()
        self.assertTrue(client.create_order.called)
        self.assertEqual(len(result['executed']), 1)

    def test_veto_enabled_and_agent_fails_open_allows_the_buy(self):
        """A failed agent call (agents/research_agent.py's own _fail_open)
        returns veto=False, failed=True -- scan_and_execute() must treat
        this identically to a clean non-veto, per the approved plan's 'fail
        toward the deterministic path' rule. Confirms the wiring doesn't
        special-case 'failed' into a block."""
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-789'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]), \
             patch('trader.research_propose', return_value={'veto': False, 'confidence': None, 'reasoning': 'agent call failed: timeout', 'risk_flags': [], 'failed': True}):
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertTrue(client.create_order.called)
        self.assertEqual(len(result['executed']), 1)

    def test_agent_not_called_when_max_positions_already_reached(self):
        """Efficiency/cost guard from the plan: the veto check sits AFTER
        the existing free guards, so a buy that would be skipped anyway
        (max positions here) never costs an API call."""
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        client = MagicMock()
        client.get_positions.return_value = [{'symbol': 'ETHUSD', 'asset_class': 'crypto'}]
        client.get_account.return_value = {'buying_power': '100000'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]), \
             patch('trader.research_propose') as mock_propose:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=1)
        mock_propose.assert_not_called()
        client.create_order.assert_not_called()

    def test_agent_not_called_when_buying_power_insufficient(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '10'}  # below position_size_usd
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]), \
             patch('trader.research_propose') as mock_propose:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        mock_propose.assert_not_called()
        client.create_order.assert_not_called()

    def test_pending_order_for_symbol_blocks_a_new_buy(self):
        """Real bug found in a full fleet review 2026-09-03: a prior buy
        order still open/pending (halted symbol, processing delay) wasn't
        checked before placing a new one for the same symbol -- a real
        double-buy path since get_positions() alone can't see it yet."""
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = [{'id': 'ord-1', 'symbol': 'BTC/USD', 'status': 'accepted'}]
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]):
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        client.get_orders.assert_called_once_with(status='open')
        client.create_order.assert_not_called()
        self.assertEqual(result['executed'], [])

    def test_no_pending_orders_allows_the_buy(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = []
        client.create_order.return_value = {'id': 'order-999'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]):
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertTrue(client.create_order.called)
        self.assertEqual(len(result['executed']), 1)

    def test_a_pending_order_for_a_different_symbol_does_not_block_this_one(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = [{'id': 'ord-1', 'symbol': 'ETH/USD', 'status': 'accepted'}]
        client.create_order.return_value = {'id': 'order-999'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]):
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertTrue(client.create_order.called)
        self.assertEqual(len(result['executed']), 1)

    def test_research_agent_exception_fails_open_and_allows_the_buy(self):
        """Real bug found in a full fleet review 2026-09-03: research_propose()
        had no try/except at its call site (unlike _handle_reentry's own
        call), so any exception there -- not just the failure modes
        research_agent.py already catches internally -- used to abort
        scan_and_execute() for every remaining signal that tick."""
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-1'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]), \
             patch('trader.research_propose', side_effect=TypeError('unexpected internal error')):
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertTrue(client.create_order.called)
        self.assertEqual(len(result['executed']), 1)

    def test_research_agent_exception_on_one_symbol_does_not_abort_the_next_signal(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        second_signal = {'symbol': 'ETH/USD', 'signal': 'buy', 'reason': 'Donchian breakout', 'price': 3000.0, 'rsi': 82.0}
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.create_order.return_value = {'id': 'order-1'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL, second_signal]), \
             patch('trader.research_propose', side_effect=[TypeError('boom'), {'veto': False, 'confidence': None, 'reasoning': 'ok', 'risk_flags': [], 'failed': False}]):
            result = tm.scan_and_execute(watchlist=['BTC/USD', 'ETH/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(client.create_order.call_count, 2)
        self.assertEqual(len(result['executed']), 2)

    def test_order_that_comes_back_rejected_is_not_recorded_as_executed(self):
        """Real bug found in a full fleet review 2026-09-03: create_order()'s
        200/201 HTTP status only confirms Alpaca ACCEPTED the request, not
        that the order will fill -- but a same-response terminal status
        (immediate rejection) is knowable right away and must not be treated
        as a successful buy."""
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = []
        client.create_order.return_value = {'id': 'order-1', 'status': 'rejected'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]):
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(result['executed'], [])
        self.assertTrue(any('rejected' in e for e in result['errors']))

    def test_order_that_comes_back_accepted_is_recorded_as_before(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = []
        client.create_order.return_value = {'id': 'order-1', 'status': 'accepted'}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[BUY_SIGNAL]):
            result = tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(len(result['executed']), 1)

    def test_stale_reentry_fired_with_no_position_or_order_is_cleared(self):
        """Real gap found in a full fleet review 2026-09-03: if an earlier
        buy attempt marked reentry_fired but never actually resulted in a
        position (e.g. an order accepted then rejected asynchronously,
        after the immediate-rejection check already added elsewhere),
        that entry could sit there indefinitely, silently blocking a real
        future re-entry. Now self-heals within one scan cycle."""
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = []
        tm = make_manager(client)
        tm.reentry_fired = {'BTCUSD'}
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[]), \
             patch.object(tm, '_save_reentry_state') as mock_save:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(tm.reentry_fired, set())
        mock_save.assert_called_once()

    def test_stale_position_methods_with_no_position_or_order_is_cleared(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = []
        tm = make_manager(client)
        tm.position_methods = {'BTCUSD': 'bollinger'}
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[]), \
             patch.object(tm, '_save_position_methods') as mock_save:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(tm.position_methods, {})
        mock_save.assert_called_once()

    def test_reentry_fired_for_a_real_open_position_is_not_cleared(self):
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = [{'symbol': 'BTCUSD', 'asset_class': 'crypto'}]
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = []
        tm = make_manager(client)
        tm.reentry_fired = {'BTCUSD'}
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[]), \
             patch.object(tm, '_save_reentry_state') as mock_save:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(tm.reentry_fired, {'BTCUSD'})
        mock_save.assert_not_called()

    def test_reentry_fired_with_a_still_open_order_is_not_cleared(self):
        """A genuinely still-pending order (not yet filled, not yet
        rejected) must not be mistaken for a failed one just because
        there's no position yet."""
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = [{'id': 'ord-1', 'symbol': 'BTC/USD', 'status': 'accepted'}]
        tm = make_manager(client)
        tm.reentry_fired = {'BTCUSD'}
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[]), \
             patch.object(tm, '_save_reentry_state') as mock_save:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(tm.reentry_fired, {'BTCUSD'})
        mock_save.assert_not_called()

    def test_reconciliation_never_touches_a_symbol_outside_this_watchlist(self):
        """Safety scoping: reentry_fired/position_methods aren't asset-
        class-segregated, but current_symbols/pending_order_symbols in any
        one scan_and_execute() call only cover that call's own watchlist --
        a stale-looking entry for a symbol scanning crypto knows nothing
        about (e.g. a stock) must be left alone, not wrongly cleared."""
        settings.RESEARCH_AGENT_VETO_ENABLED = False
        client = MagicMock()
        client.get_positions.return_value = []
        client.get_account.return_value = {'buying_power': '100000'}
        client.get_orders.return_value = []
        tm = make_manager(client)
        tm.reentry_fired = {'AAPL'}  # not in this crypto-only watchlist
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[]), \
             patch.object(tm, '_save_reentry_state') as mock_save:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        self.assertEqual(tm.reentry_fired, {'AAPL'})
        mock_save.assert_not_called()

    def test_sell_signals_never_invoke_the_agent(self):
        """Per the approved plan, the agent only ever reviews buys the
        scanner already found -- it has no role in exits.

        The sell branch unconditionally calls _save_peak_prices/
        _save_position_opened_at/_save_position_methods regardless of asset
        class (real file writes to the repo root) -- all three, plus
        _record_trade_outcome, must be mocked here or this test would write
        to real local state files as a side effect, exactly the class of
        mistake LESSONS.md #7 warns about. (Caught by running this test:
        it did write empty peak_prices_state.json/position_method_state.json/
        position_opened_state.json to disk before this fix -- harmless
        empty-dict writes since nothing real was tracked, but a real bug in
        test isolation, not just a hypothetical risk.)"""
        settings.RESEARCH_AGENT_VETO_ENABLED = True
        sell_signal = {'symbol': 'BTC/USD', 'signal': 'sell', 'reason': 'reverted to mid-band', 'price': 78000.0}
        client = MagicMock()
        client.get_positions.return_value = [{'symbol': 'BTCUSD', 'asset_class': 'crypto'}]
        client.get_account.return_value = {'buying_power': '100000'}
        client.close_position.return_value = {}
        tm = make_manager(client)
        with patch.object(trader.OpportunityScanner, 'scan', return_value=[sell_signal]), \
             patch.object(tm, '_record_trade_outcome'), \
             patch.object(tm, '_save_peak_prices'), \
             patch.object(tm, '_save_position_opened_at'), \
             patch.object(tm, '_save_position_methods'), \
             patch.object(tm, '_save_reentry_state'), \
             patch('trader.research_propose') as mock_propose:
            tm.scan_and_execute(watchlist=['BTC/USD'], position_size_usd=500, max_positions=2)
        mock_propose.assert_not_called()
        self.assertTrue(client.close_position.called)


if __name__ == '__main__':
    unittest.main()
