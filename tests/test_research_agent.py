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


def _article(headline='', summary='', created_at='2026-09-02T12:00:00Z', symbols=None):
    return {'headline': headline, 'summary': summary, 'created_at': created_at,
            'symbols': symbols if symbols is not None else ['AAPL']}


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

    def test_broad_multi_company_article_is_ignored(self):
        """Real bug found live 2026-09-02: a keyword match inside an article
        tagged with many symbols (a comparison/roundup piece, not news about
        this symbol specifically) must not count. Reproduces the original
        case that caused it (an 8-symbol article vetoed an unrelated NVDA
        entry), with 'layoffs' standing in for the 'warns' match that
        actually triggered it -- 'warns' was removed from the keyword list
        2026-09-03 after a second, unrelated false-catch, but the multi-
        symbol filter this test verifies is unrelated to which keyword
        matched."""
        client = MagicMock()
        client.get_news.return_value = [_article(
            headline="Analyst Note: Sector-Wide Layoffs Loom as Self-Driving Race Heats Up",
            symbols=['AMZN', 'BIDU', 'GOOG', 'GOOGL', 'NVDA', 'SKHY', 'TSLA', 'WRD'],
        )]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertFalse(result['veto'])
        self.assertEqual(result['risk_flags'], [])

    def test_headline_naming_unmapped_other_companies_is_ignored(self):
        """Real bug found live 2026-09-04: a DOJ beef-pricing-probe roundup
        vetoed COST on 'probe' six times in a row. Alpaca tagged it with only
        3 symbols (AMZN, COST, WMT) -- right at MAX_ARTICLE_SYMBOLS, so the
        symbol-count filter alone didn't catch it -- even though the
        headline itself says "...and 5 Other Retail Giants", 8 companies in
        substance. Must be skipped on the headline text alone, independent
        of tag count."""
        client = MagicMock()
        client.get_news.return_value = [_article(
            headline="Trump's DOJ Expands Beef Price Probe to Walmart, Costco, Amazon and 5 Other Retail Giants",
            symbols=['AMZN', 'COST', 'WMT'],
        )]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertFalse(result['veto'])
        self.assertEqual(result['risk_flags'], [])

    def test_headline_with_no_other_companies_mention_still_vetoes(self):
        """Guards against the new pattern being so broad it swallows normal
        headlines that merely contain the word 'other' without the roundup
        construction -- must still veto a genuinely focused article."""
        client = MagicMock()
        client.get_news.return_value = [_article(
            headline='Company faces lawsuit from other former employees',
            symbols=['AAPL'],
        )]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['veto'])

    def test_keyword_inside_a_longer_word_does_not_match(self):
        """Real bug found live 2026-09-03: 'sues' is a substring of 'issues',
        so a plain `in` check vetoed completely benign headlines like this
        one. Now word-boundary matched -- 'issues' must not trigger 'sues'."""
        client = MagicMock()
        client.get_news.return_value = [_article(headline='Apple issues strong holiday guidance')]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertFalse(result['veto'])
        self.assertEqual(result['risk_flags'], [])

    def test_keyword_as_a_real_standalone_word_still_matches(self):
        """Word-boundary matching must not become so strict it stops matching
        the real word -- 'sues' as an actual standalone word must still veto."""
        client = MagicMock()
        client.get_news.return_value = [_article(headline='Regulator sues company over disclosure failures')]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['veto'])
        self.assertIn('sues', result['risk_flags'])

    def test_focused_article_at_the_threshold_still_counts(self):
        """MAX_ARTICLE_SYMBOLS is a boundary, not an off-by-one trap -- an
        article tagged with exactly the limit still counts as focused."""
        client = MagicMock()
        client.get_news.return_value = [_article(
            headline='Company X sued by former partner',
            symbols=['AAPL', 'MSFT', 'GOOGL'][:research_agent.MAX_ARTICLE_SYMBOLS],
        )]
        result = research_agent.propose(SIGNAL, client=client)
        self.assertTrue(result['veto'])

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
