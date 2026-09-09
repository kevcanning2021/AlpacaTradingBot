"""Tests for market_hours_notifier.py -- state-file transition detection,
no live network calls (AlpacaClient/TelegramNotifier are mocked).

Run with: python -m unittest tests.test_market_hours_notifier -v
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import market_hours_notifier as mhn


class MarketHoursNotifierTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmpdir, "market_hours_state.json")
        self.state_patch = patch.object(mhn, 'STATE_PATH', self.state_path)
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, is_open, prior_state=None):
        if prior_state is not None:
            with open(self.state_path, 'w') as f:
                json.dump(prior_state, f)

        mock_client = MagicMock()
        mock_client.get_clock.return_value = {
            'is_open': is_open, 'next_open': '2026-09-10T09:30:00-04:00', 'next_close': '2026-09-09T16:00:00-04:00',
        }
        mock_notifier = MagicMock()

        with patch.object(mhn, 'AlpacaClient', return_value=mock_client), \
             patch.object(mhn, 'TelegramNotifier', return_value=mock_notifier):
            mhn.main()

        with open(self.state_path) as f:
            state = json.load(f)
        return mock_notifier, state

    def test_first_ever_run_does_not_notify(self):
        """No prior state file -- nothing to compare against, must not guess."""
        notifier, state = self._run(is_open=True, prior_state=None)
        notifier.send.assert_not_called()
        self.assertEqual(state, {'is_open': True})

    def test_no_transition_does_not_notify(self):
        notifier, state = self._run(is_open=True, prior_state={'is_open': True})
        notifier.send.assert_not_called()
        self.assertEqual(state, {'is_open': True})

    def test_closed_to_open_transition_notifies(self):
        notifier, state = self._run(is_open=True, prior_state={'is_open': False})
        notifier.send.assert_called_once()
        subject, body = notifier.send.call_args[0]
        self.assertIn('open', subject.lower())
        self.assertEqual(state, {'is_open': True})

    def test_open_to_closed_transition_notifies(self):
        notifier, state = self._run(is_open=False, prior_state={'is_open': True})
        notifier.send.assert_called_once()
        subject, body = notifier.send.call_args[0]
        self.assertIn('closed', subject.lower())
        self.assertEqual(state, {'is_open': False})

    def test_corrupt_state_file_treated_as_unknown_not_a_crash(self):
        with open(self.state_path, 'w') as f:
            f.write("{not valid json")

        mock_client = MagicMock()
        mock_client.get_clock.return_value = {'is_open': True, 'next_open': '', 'next_close': ''}
        mock_notifier = MagicMock()
        with patch.object(mhn, 'AlpacaClient', return_value=mock_client), \
             patch.object(mhn, 'TelegramNotifier', return_value=mock_notifier):
            mhn.main()  # must not raise

        mock_notifier.send.assert_not_called()  # unknown prior state -> treated like first run


if __name__ == '__main__':
    unittest.main()
