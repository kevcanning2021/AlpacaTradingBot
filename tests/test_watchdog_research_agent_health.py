"""Regression tests for watchdog.py: check_research_agent_health(), added
2026-09-05 after a real live miss -- SOFI's COST was vetoed 6 consecutive
hourly checks on the identical stale/broad-roundup article (see
agents/research_agent.py's _OTHER_COMPANIES_PATTERN fix for the underlying
bug) before the user spotted it on the dashboard. Nothing on this VPS was
watching the research agent's own decision output for a stuck pattern, only
whether the bots were running and trading -- this closes that gap.

Run with: python -m unittest tests.test_watchdog_research_agent_health -v
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import watchdog


def _write_decisions(path, decisions):
    with open(path, 'w') as f:
        json.dump(decisions, f)


def _decision(veto, reasoning, age_hours=0):
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    return {'veto': veto, 'confidence': None, 'reasoning': reasoning, 'risk_flags': [],
            'failed': False, 'timestamp': timestamp}


class ResearchAgentHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, 'agent_decisions_state.json')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_file_reports_no_issues(self):
        """The file doesn't exist until a bot's research agent has run at
        least once -- a fresh deploy or a symbol that's never been checked
        must not be treated as a problem."""
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        self.assertEqual(issues, [])

    def test_fewer_than_threshold_entries_is_not_flagged(self):
        _write_decisions(self.path, {'COST': [_decision(True, 'same reasoning')] * (watchdog.STUCK_VETO_THRESHOLD - 1)})
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        self.assertEqual(issues, [])

    def test_repeated_identical_veto_at_threshold_is_flagged(self):
        """Reproduces the real COST case: N consecutive vetoes, same
        reasoning string every time -- exactly what stayed invisible."""
        _write_decisions(self.path, {'COST': [_decision(True, 'Found 1 recent article(s) with red-flag keywords [\'probe\']: "..."')] * watchdog.STUCK_VETO_THRESHOLD})
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        stuck = [i for i in issues if 'stuck_veto:COST' in i[0]]
        self.assertEqual(len(stuck), 1)
        self.assertIn('SOFI', stuck[0][1])
        self.assertIn('COST', stuck[0][1])

    def test_veto_reasoning_that_changes_each_check_is_not_flagged(self):
        """Fresh evidence each check (different article each time) is
        exactly the healthy case this must not false-positive on."""
        _write_decisions(self.path, {'COST': [
            _decision(True, f'article #{i}') for i in range(watchdog.STUCK_VETO_THRESHOLD)
        ]})
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        self.assertEqual([i for i in issues if 'stuck_veto' in i[0]], [])

    def test_streak_broken_by_a_non_veto_is_not_flagged(self):
        """Only the tail matters -- an old stuck streak that has since
        cleared (most recent decision is a clean non-veto) must not still
        alert on stale history earlier in the list."""
        history = [_decision(True, 'same reasoning')] * 5 + [_decision(False, 'No red-flag keywords found')]
        _write_decisions(self.path, {'COST': history})
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        self.assertEqual([i for i in issues if 'stuck_veto' in i[0]], [])

    def test_a_genuinely_fresh_persistent_flag_is_flagged_too(self):
        """Deliberately not trying to be smarter than the underlying check --
        a real, still-valid red flag (e.g. Nova's real MSFT revenue
        restatement, correctly vetoed 3 times running) trips this the same
        way a stale one does. That's intentional: this surfaces repetition
        for a human glance, it doesn't judge correctness -- see the
        STUCK_VETO_THRESHOLD comment in watchdog.py."""
        _write_decisions(self.path, {'MSFT': [_decision(True, 'Found 1 recent article(s) with red-flag keywords [\'restated\']: "..."')] * watchdog.STUCK_VETO_THRESHOLD})
        issues = watchdog.check_research_agent_health('trading2', 'Trading 2.0', self.path)
        self.assertEqual(len(issues), 1)

    def test_old_stale_streak_past_max_age_is_not_flagged(self):
        """The real gap this closes: a symbol a bot stops re-checking
        entirely sits at the tail of its own history forever by position --
        without a recency check this would alert on days-old activity
        forever, indistinguishable from an ongoing block. Age must gate on
        the most recent decision's own timestamp, not just list position."""
        old = watchdog.STUCK_VETO_MAX_AGE_HOURS + 1
        _write_decisions(self.path, {'COST': [_decision(True, 'same reasoning', age_hours=old)] * watchdog.STUCK_VETO_THRESHOLD})
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        self.assertEqual([i for i in issues if 'stuck_veto' in i[0]], [])

    def test_entry_missing_a_timestamp_is_skipped_not_crashed(self):
        """Defensive only -- every real decision has always carried a
        timestamp (agents/state.py / bot/research_agent.py both stamp it
        unconditionally), but a missing/malformed one must fail closed
        (skip) rather than raise and abort the whole watchdog run."""
        entries = [{'veto': True, 'reasoning': 'x', 'risk_flags': [], 'failed': False}] * watchdog.STUCK_VETO_THRESHOLD
        _write_decisions(self.path, {'COST': entries})
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        self.assertEqual([i for i in issues if 'stuck_veto' in i[0]], [])

    def test_malformed_json_reports_an_issue_without_crashing(self):
        with open(self.path, 'w') as f:
            f.write('{not valid json')
        issues = watchdog.check_research_agent_health('sofi', 'SOFI', self.path)
        self.assertEqual(len(issues), 1)
        self.assertIn('research_agent_read_error', issues[0][0])


if __name__ == '__main__':
    unittest.main()
