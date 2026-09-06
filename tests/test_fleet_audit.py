"""Regression tests for /api/fleet-audit, added 2026-09-06 -- surfaces each
bot's own daily_fleet_audit.py log (cron, independent of any Claude session)
on the dashboard itself, so "how'd the run go" doesn't require SSHing in.

Run with: python -m unittest tests.test_fleet_audit -v
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from dashboard import app, cache, config


def _fake_request():
    return MagicMock()


class LoadFleetAuditTests(unittest.TestCase):
    def setUp(self):
        self._orig = dict(config.FLEET_AUDIT_LOG_PATHS)

    def tearDown(self):
        config.FLEET_AUDIT_LOG_PATHS.clear()
        config.FLEET_AUDIT_LOG_PATHS.update(self._orig)

    def _write_log(self, entries):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(entries, f)
            return f.name

    def test_returns_the_most_recent_entry_per_bot_tagged_with_bot(self):
        path = self._write_log([
            {'date': '2026-09-05', 'backtest': {'trade_count': 100}},
            {'date': '2026-09-06', 'backtest': {'trade_count': 172}},
        ])
        try:
            config.FLEET_AUDIT_LOG_PATHS.clear()
            config.FLEET_AUDIT_LOG_PATHS['main'] = path
            result = app._load_fleet_audit()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['date'], '2026-09-06')  # latest, not the whole history
            self.assertEqual(result[0]['bot'], 'main')
        finally:
            os.unlink(path)

    def test_missing_file_for_one_bot_is_skipped_not_an_error(self):
        """A bot whose first cron run hasn't fired yet, or whose log was
        never seeded -- must not be treated as a fault."""
        config.FLEET_AUDIT_LOG_PATHS.clear()
        config.FLEET_AUDIT_LOG_PATHS['main'] = '/nonexistent/fleet_audit_log.json'
        self.assertEqual(app._load_fleet_audit(), [])

    def test_one_unreadable_bot_does_not_block_the_others(self):
        good_path = self._write_log([{'date': '2026-09-06', 'backtest': {'trade_count': 222}}])
        bad_path = self._write_log([])  # will be corrupted below
        with open(bad_path, 'w') as f:
            f.write('{not valid json')
        try:
            config.FLEET_AUDIT_LOG_PATHS.clear()
            config.FLEET_AUDIT_LOG_PATHS['nova'] = good_path
            config.FLEET_AUDIT_LOG_PATHS['main'] = bad_path
            result = app._load_fleet_audit()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['bot'], 'nova')
        finally:
            os.unlink(good_path)
            os.unlink(bad_path)

    def test_empty_log_for_a_bot_contributes_nothing(self):
        path = self._write_log([])
        try:
            config.FLEET_AUDIT_LOG_PATHS.clear()
            config.FLEET_AUDIT_LOG_PATHS['sofi'] = path
            self.assertEqual(app._load_fleet_audit(), [])
        finally:
            os.unlink(path)


class FleetAuditEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = dict(config.FLEET_AUDIT_LOG_PATHS)
        cache._store.clear()

    def tearDown(self):
        config.FLEET_AUDIT_LOG_PATHS.clear()
        config.FLEET_AUDIT_LOG_PATHS.update(self._orig)
        cache._store.clear()

    async def test_endpoint_returns_json_list(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([{'date': '2026-09-06', 'backtest': {'trade_count': 99}}], f)
            path = f.name
        try:
            config.FLEET_AUDIT_LOG_PATHS.clear()
            config.FLEET_AUDIT_LOG_PATHS['sofi'] = path
            response = await app.fleet_audit(_fake_request())
            body = json.loads(response.body)
            self.assertEqual(len(body), 1)
            self.assertEqual(body[0]['bot'], 'sofi')
        finally:
            os.unlink(path)

    async def test_endpoint_returns_empty_list_when_no_bot_has_logged_yet(self):
        config.FLEET_AUDIT_LOG_PATHS.clear()
        config.FLEET_AUDIT_LOG_PATHS['main'] = '/nonexistent/path.json'
        response = await app.fleet_audit(_fake_request())
        self.assertEqual(json.loads(response.body), [])


if __name__ == '__main__':
    unittest.main()
