"""Regression tests for agents/research_agent.py -- rewritten 2026-09-02 when
the Claude + web-search version was replaced with a free, keyword-based news
check (real dollar cost was adding up too fast for what it provided). No
network calls, ever -- client.get_news is a MagicMock in every test.

Run with: python -m unittest tests.test_research_agent -v
"""
import os
import unittest
from unittest.mock import MagicMock

from agents import research_agent
from agents.state import AGENT_DECISIONS_FILE, load_agent_decisions

SIGNAL = {'symbol': 'AAPL', 'signal': 'buy', 'reason': 'Bollinger oversold bounce', 'price': 230.5, 'rsi': 38}


def _article(headline='', summary='', created_at='2026-09-02T12:00:00Z'):
    return {'headline': headline, 'summary': summary, 'created_at': created_at}


class ResearchAgentTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(AGENT_DECISIONS_FILE):
            os.remove(AGENT_DECISIONS_FILE)

    def tearDown(self):
        if os.path.exists(AGENT_DECISIONS_FILE):
            os.remove(AGENT_DECISIONS_FILE)

    def test_no_red_flags_does_not_veto(self):
        client = MagicMock()
        client.get_news.return_value = [
            _article(headline='Apple unveils new product lineup'),
            _article(headline='Analysts raise price target on AAPL'),
        ]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertFalse(result['veto'])
        self.assertFalse(result['failed'])
        self.assertEqual(result['risk_flags'], [])
        self.assertEqual(load_agent_decisions()['AAPL'][0]['veto'], False)

    def test_red_flag_in_headline_vetoes(self):
        client = MagicMock()
        client.get_news.return_value = [_article(headline='Apple hit with new antitrust lawsuit')]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['veto'])
        self.assertFalse(result['failed'])
        self.assertIn('lawsuit', result['risk_flags'])
        self.assertIn('lawsuit', result['reasoning'])

    def test_red_flag_in_summary_also_vetoes(self):
        """Keyword matching checks headline + summary combined -- a flag
        buried in the summary, not the headline, must still be caught."""
        client = MagicMock()
        client.get_news.return_value = [
            _article(headline='Apple Q3 update', summary='The company disclosed an ongoing SEC investigation.')
        ]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['veto'])
        self.assertIn('investigation', result['risk_flags'])

    def test_matching_is_case_insensitive(self):
        client = MagicMock()
        client.get_news.return_value = [_article(headline='COMPANY FILES FOR BANKRUPTCY PROTECTION')]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['veto'])
        self.assertIn('bankruptcy', result['risk_flags'])

    def test_no_news_found_is_not_a_failure(self):
        """Absence of news is a normal state, not an error -- same
        convention as every other 'nothing found' check in this project."""
        client = MagicMock()
        client.get_news.return_value = []
        result = research_agent.propose(SIGNAL, client=client)
        self.assertFalse(result['veto'])
        self.assertFalse(result['failed'])

    def test_no_client_fails_open_without_calling_anything(self):
        result = research_agent.propose(SIGNAL, client=None)
        self.assertTrue(result['failed'])
        self.assertFalse(result['veto'])

    def test_news_fetch_exception_fails_open(self):
        client = MagicMock()
        client.get_news.side_effect = RuntimeError('connection reset')
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['failed'])
        self.assertFalse(result['veto'])
        self.assertIn('connection reset', result['reasoning'])

    def test_multiple_articles_only_flagged_ones_reported(self):
        """'downgrade' is a substring of 'downgraded' -- the list intentionally
        carries a few overlapping word forms (readability over minimalism),
        so the matched keyword reported is whichever form appears earliest in
        RED_FLAG_KEYWORDS, not necessarily the exact word used in the
        headline. The real behavior under test here is that only the flagged
        article contributes a risk flag, not the clean ones either side of it."""
        client = MagicMock()
        client.get_news.return_value = [
            _article(headline='Apple announces new store openings'),
            _article(headline='Apple downgraded by analyst'),
            _article(headline='Apple wins design award'),
        ]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['veto'])
        self.assertEqual(len(result['risk_flags']), 1)
        self.assertIn('downgrad', result['risk_flags'][0])  # matches 'downgrade' or 'downgraded'

    def test_recent_bars_accepted_but_unused(self):
        """Interface-compatibility parameter only -- this version has
        nothing to do with recent price action."""
        client = MagicMock()
        client.get_news.return_value = []
        result = research_agent.propose(SIGNAL, recent_bars=[{'c': 228.0}], client=client)
        self.assertFalse(result['failed'])

    def test_lookback_window_passed_to_news_fetch(self):
        client = MagicMock()
        client.get_news.return_value = []
        research_agent.propose(SIGNAL, client=client)
        client.get_news.assert_called_once_with('AAPL', lookback_hours=research_agent.LOOKBACK_HOURS,
                                                  limit=research_agent.NEWS_LIMIT)

    def test_agent_decisions_state_file_is_additive_only(self):
        """Hard rule from the original design: this module must never touch
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
