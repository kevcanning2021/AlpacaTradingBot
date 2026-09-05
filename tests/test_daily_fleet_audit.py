"""Regression tests for daily_fleet_audit.py's drift detection and log
persistence -- the two pieces of this script that aren't already validated
by the 2026-09-05 Fleet Audit's own backtest numbers (simulate_dual_signal
is the same engine used there, reused as-is).

Run with: python -m unittest tests.test_daily_fleet_audit -v
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import daily_fleet_audit as audit


class DriftDetectionTests(unittest.TestCase):
    def test_first_ever_run_reports_no_drift(self):
        """Nothing to compare against yet -- must not flag every value as
        'changed' just because there's no prior entry."""
        result = audit.detect_drift('Main', ['AAPL', 'MSFT'], prior_entry=None)
        self.assertFalse(result['changed'])

    def test_identical_watchlist_is_not_flagged(self):
        prior = {'config_snapshot': {'watchlist': ['AAPL', 'MSFT'], 'buy_rsi_max': 65, 'sell_rsi_min': 80}}
        with patch('daily_fleet_audit.BUY_RSI_MAX', 65), patch('daily_fleet_audit.SELL_RSI_MIN', 80):
            result = audit.detect_drift('Main', ['MSFT', 'AAPL'], prior_entry=prior)  # different order, same set
        self.assertFalse(result['changed'])

    def test_added_symbol_is_flagged(self):
        """Reproduces the real 2026-09-05 finding this exists to catch: a
        watchlist growing between two runs."""
        prior = {'config_snapshot': {'watchlist': ['AAPL', 'MSFT'], 'buy_rsi_max': 65, 'sell_rsi_min': 80}}
        with patch('daily_fleet_audit.BUY_RSI_MAX', 65), patch('daily_fleet_audit.SELL_RSI_MIN', 80):
            result = audit.detect_drift('Main', ['AAPL', 'MSFT', 'IWM'], prior_entry=prior)
        self.assertTrue(result['changed'])

    def test_threshold_change_is_flagged(self):
        prior = {'config_snapshot': {'watchlist': ['AAPL'], 'buy_rsi_max': 65, 'sell_rsi_min': 80}}
        with patch('daily_fleet_audit.BUY_RSI_MAX', 70), patch('daily_fleet_audit.SELL_RSI_MIN', 80):
            result = audit.detect_drift('Main', ['AAPL'], prior_entry=prior)
        self.assertTrue(result['changed'])


class LogPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmpdir, 'fleet_audit_log.json')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_missing_log_returns_empty_list(self):
        with patch('daily_fleet_audit.LOG_FILE', self.log_path):
            self.assertEqual(audit.load_log(), [])

    def test_save_then_load_round_trips(self):
        with patch('daily_fleet_audit.LOG_FILE', self.log_path):
            audit.save_log([{'date': '2026-09-05', 'backtest': {'trade_count': 10}}])
            loaded = audit.load_log()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]['date'], '2026-09-05')

    def test_log_is_capped_at_max_entries(self):
        """A daily log left unbounded would grow forever -- confirms the cap
        actually trims old entries, keeping the most recent ones."""
        entries = [{'date': f'day-{i}'} for i in range(audit.MAX_LOG_ENTRIES + 5)]
        with patch('daily_fleet_audit.LOG_FILE', self.log_path):
            audit.save_log(entries)
            loaded = audit.load_log()
        self.assertEqual(len(loaded), audit.MAX_LOG_ENTRIES)
        self.assertEqual(loaded[-1]['date'], f'day-{audit.MAX_LOG_ENTRIES + 4}')  # newest kept, not oldest

    def test_malformed_log_file_is_treated_as_empty_not_a_crash(self):
        with open(self.log_path, 'w') as f:
            f.write('{not valid json')
        with patch('daily_fleet_audit.LOG_FILE', self.log_path):
            self.assertEqual(audit.load_log(), [])


if __name__ == '__main__':
    unittest.main()
