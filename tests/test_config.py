"""Regression test for dashboard/config.py, added 2026-09-03 after a full
fleet review found RESEARCH_AGENT_DECISIONS_PATHS['sofi'] still pointed at
the retired pdt15rev-bot's directory instead of sofi-bot's own state file
(pdt15rev-bot was retired 2026-09-02, replaced by sofi-bot).

Run with: python -m unittest tests.test_config -v
"""
import unittest

from dashboard import config


class ResearchDecisionsPathsTests(unittest.TestCase):
    def test_sofi_path_points_at_the_live_sofi_bot_not_the_retired_pdt15rev_bot(self):
        self.assertNotIn('pdt15rev-bot', config.RESEARCH_AGENT_DECISIONS_PATHS['sofi'])
        self.assertIn('sofi-bot', config.RESEARCH_AGENT_DECISIONS_PATHS['sofi'])


if __name__ == '__main__':
    unittest.main()
