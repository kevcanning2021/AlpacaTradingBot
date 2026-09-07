"""Regression tests for /api/market-status, added 2026-09-07 after a Labor
Day market holiday looked exactly like a stuck bot on the dashboard --
crypto kept scanning 24/7 the whole time, so nothing signaled that the
stock scan had correctly gone quiet for the day rather than hung.

Run with: python -m unittest tests.test_market_status -v
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from dashboard import app, cache


def _fake_request():
    return MagicMock()


class MarketStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._store.clear()

    def tearDown(self):
        cache._store.clear()

    async def test_market_open_is_reported_as_such(self):
        client = MagicMock()
        client.get_clock.return_value = {
            'is_open': True, 'next_open': '2026-09-08T09:30:00-04:00', 'next_close': '2026-09-07T16:00:00-04:00',
        }
        with patch('dashboard.app.get_client', return_value=client):
            response = await app.market_status(_fake_request())
        body = json.loads(response.body)
        self.assertTrue(body['is_open'])

    async def test_market_closed_holiday_is_reported_with_next_open(self):
        """The real 2026-09-07 case: is_open false, next_open a full day
        away, not the usual overnight gap -- the frontend uses next_open to
        tell a holiday apart from an ordinary after-hours close."""
        client = MagicMock()
        client.get_clock.return_value = {
            'is_open': False, 'next_open': '2026-09-08T09:30:00-04:00', 'next_close': '2026-09-08T16:00:00-04:00',
        }
        with patch('dashboard.app.get_client', return_value=client):
            response = await app.market_status(_fake_request())
        body = json.loads(response.body)
        self.assertFalse(body['is_open'])
        self.assertEqual(body['next_open'], '2026-09-08T09:30:00-04:00')

    async def test_upstream_failure_returns_502_not_a_crash(self):
        client = MagicMock()
        client.get_clock.side_effect = Exception('Failed to get clock: 401 - unauthorized')
        with patch('dashboard.app.get_client', return_value=client):
            response = await app.market_status(_fake_request())
        self.assertEqual(response.status_code, 502)


if __name__ == '__main__':
    unittest.main()
