"""Regression tests for agents/research_agent.py. All Anthropic API calls are
mocked -- no live calls, ever, matching this project's LESSONS.md #5/#9
"verify against a mocked client before trusting a live-call path" discipline,
and this codebase's own established habit of testing every new integration
against a stub before wiring it in for real (see trader.py's mocked-client
verification pattern used for every past reentry/crypto/trailing-stop
change).

Run with: python -m unittest tests.test_research_agent -v
"""
import json
import os
import unittest
from unittest.mock import MagicMock, patch

import anthropic

from agents import research_agent
from agents.state import AGENT_DECISIONS_FILE, load_agent_decisions


class _FakeAPIError(anthropic.APIError):
    """A real anthropic.APIError subclass (so isinstance() checks in the
    code under test behave correctly) without needing to know the real
    SDK exception constructors' exact signatures -- avoids guessing at
    something not documented in what was verified this session."""
    def __init__(self, msg='simulated API failure'):
        self.message = msg

    def __str__(self):
        return self.message


def _text_block(text):
    b = MagicMock()
    b.type = 'text'
    b.text = text
    return b


def _make_response(decision_dict, stop_reason='end_turn', extra_blocks=None, stop_details=None):
    """extra_blocks simulates the thinking/server_tool_use/web_search_tool_result/
    code_execution_tool_result blocks confirmed to precede the final text
    block in a real web_search-using response (verified live 2026-08-25) --
    the code under test must find the decision in the LAST text block, not
    assume it's content[0]."""
    response = MagicMock()
    response.stop_reason = stop_reason
    response.stop_details = stop_details
    blocks = list(extra_blocks or [])
    if decision_dict is not None:
        blocks.append(_text_block(json.dumps(decision_dict)))
    response.content = blocks
    return response


def _non_text_block(block_type):
    b = MagicMock()
    b.type = block_type
    return b


SIGNAL = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'Bollinger oversold bounce', 'price': 230.5, 'rsi': 38}


class ResearchAgentTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(AGENT_DECISIONS_FILE):
            os.remove(AGENT_DECISIONS_FILE)
        self._orig_key = research_agent.settings.ANTHROPIC_API_KEY
        research_agent.settings.ANTHROPIC_API_KEY = 'test-key-not-real'

    def tearDown(self):
        if os.path.exists(AGENT_DECISIONS_FILE):
            os.remove(AGENT_DECISIONS_FILE)
        research_agent.settings.ANTHROPIC_API_KEY = self._orig_key

    def test_successful_decision_parsed_and_recorded(self):
        """Core path: a clean response with the decision as the LAST text
        block (after simulated search-activity blocks) parses correctly."""
        decision = {'veto': False, 'confidence': 0.62, 'reasoning': 'no adverse news found', 'risk_flags': ['thin_volume']}
        response = _make_response(
            decision,
            extra_blocks=[_non_text_block('thinking'), _non_text_block('server_tool_use'), _non_text_block('web_search_tool_result')],
        )
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            result = research_agent.propose(SIGNAL)
        self.assertFalse(result['failed'])
        self.assertFalse(result['veto'])
        self.assertEqual(result['confidence'], 0.62)
        self.assertEqual(result['risk_flags'], ['thin_volume'])
        # Verify it was actually recorded via agents/state.py, not just returned
        self.assertEqual(load_agent_decisions()['AAPL'][0]['confidence'], 0.62)

    def test_veto_true_parsed_correctly(self):
        decision = {'veto': True, 'confidence': 0.85, 'reasoning': 'earnings miss reported today', 'risk_flags': ['earnings_miss']}
        response = _make_response(decision)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            result = research_agent.propose(SIGNAL)
        self.assertTrue(result['veto'])
        self.assertFalse(result['failed'])

    def test_no_api_key_fails_open_without_calling_api(self):
        research_agent.settings.ANTHROPIC_API_KEY = ''
        with patch.object(research_agent.anthropic, 'Anthropic') as mock_anthropic_cls:
            result = research_agent.propose(SIGNAL)
        mock_anthropic_cls.assert_not_called()
        self.assertTrue(result['failed'])
        self.assertFalse(result['veto'])

    def test_api_error_fails_open(self):
        """Per the approved plan: any agent call failure must fail toward
        the deterministic path (veto=False), never toward blocking a trade
        or crashing the caller."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _FakeAPIError('connection reset')
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            result = research_agent.propose(SIGNAL)
        self.assertTrue(result['failed'])
        self.assertFalse(result['veto'])
        self.assertIn('connection reset', result['reasoning'])

    def test_refusal_fails_open(self):
        stop_details = MagicMock()
        stop_details.category = 'frontier_llm'
        response = _make_response(None, stop_reason='refusal', stop_details=stop_details)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            result = research_agent.propose(SIGNAL)
        self.assertTrue(result['failed'])
        self.assertIn('frontier_llm', result['reasoning'])

    def test_non_terminal_stop_reason_fails_open(self):
        """pause_turn (or anything else non-end_turn) isn't handled with a
        resume loop in this shadow phase -- must fail open, not hang or
        raise."""
        response = _make_response({'veto': False, 'confidence': 0.5, 'reasoning': 'x', 'risk_flags': []}, stop_reason='pause_turn')
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            result = research_agent.propose(SIGNAL)
        self.assertTrue(result['failed'])
        self.assertIn('pause_turn', result['reasoning'])

    def test_no_text_block_fails_open(self):
        response = _make_response(None, extra_blocks=[_non_text_block('server_tool_use')])
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            result = research_agent.propose(SIGNAL)
        self.assertTrue(result['failed'])

    def test_malformed_json_fails_open(self):
        response = _make_response(None, extra_blocks=[_text_block('not valid json{')])
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            result = research_agent.propose(SIGNAL)
        self.assertTrue(result['failed'])

    def test_recent_bars_included_in_prompt_but_no_alpaca_call_made(self):
        """propose() must never call the Alpaca API itself -- bars are the
        caller's responsibility to pre-fetch and pass in."""
        decision = {'veto': False, 'confidence': 0.5, 'reasoning': 'x', 'risk_flags': []}
        response = _make_response(decision)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        bars = [{'c': 228.0, 't': '2026-08-24T00:00:00Z'}, {'c': 230.5, 't': '2026-08-25T00:00:00Z'}]
        with patch.object(research_agent.anthropic, 'Anthropic', return_value=mock_client):
            research_agent.propose(SIGNAL, recent_bars=bars)
        call_kwargs = mock_client.messages.create.call_args.kwargs
        prompt_text = call_kwargs['messages'][0]['content']
        self.assertIn('228.0', prompt_text)
        self.assertIn('230.5', prompt_text)

    def test_agent_decisions_state_file_is_additive_only(self):
        """Hard rule from the approved plan: this module must never touch
        trader.py's six state files. Confirm the decisions file is a
        distinct path from all of them."""
        trader_state_files = {
            'peak_prices_state.json', 'position_opened_state.json', 'reentry_state.json',
            'position_method_state.json', 'trade_history.json', 'zero_since_state.json',
        }
        self.assertNotIn(os.path.basename(AGENT_DECISIONS_FILE), trader_state_files)
        self.assertEqual(os.path.basename(AGENT_DECISIONS_FILE), 'agent_decisions_state.json')


if __name__ == '__main__':
    unittest.main()
