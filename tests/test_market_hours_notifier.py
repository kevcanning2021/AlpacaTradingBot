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

    def test_failed_send_does_not_advance_state(self):
        """The bug this file exists to prevent, found live 2026-09-23.

        TelegramNotifier.send() RAISES on any non-200, and main() used to
        save state before calling it. One transient Telegram failure
        therefore advanced the state past an announcement that never went
        out, and every later run saw last_state == is_open and stayed
        silent -- the ping was lost for good, not merely delayed.

        Leaving the state untouched on failure is what makes the retry on
        the next cron run possible."""
        with open(self.state_path, 'w') as f:
            json.dump({'is_open': False}, f)

        mock_client = MagicMock()
        mock_client.get_clock.return_value = {
            'is_open': True, 'next_open': '', 'next_close': '',
        }
        mock_notifier = MagicMock()
        mock_notifier.send.side_effect = Exception('Telegram returned HTTP 500')

        with patch.object(mhn, 'AlpacaClient', return_value=mock_client),              patch.object(mhn, 'TelegramNotifier', return_value=mock_notifier):
            with self.assertRaises(Exception):
                mhn.main()

        with open(self.state_path) as f:
            self.assertEqual(json.load(f), {'is_open': False},
                              'state advanced despite the notification failing')

    def test_transition_is_retried_on_the_next_run_after_a_failure(self):
        """The half that actually matters to the user: not just that state
        held, but that the ping genuinely still arrives afterwards."""
        with open(self.state_path, 'w') as f:
            json.dump({'is_open': False}, f)

        mock_client = MagicMock()
        mock_client.get_clock.return_value = {
            'is_open': True, 'next_open': '', 'next_close': '',
        }
        failing = MagicMock()
        failing.send.side_effect = Exception('Telegram returned HTTP 500')
        with patch.object(mhn, 'AlpacaClient', return_value=mock_client),              patch.object(mhn, 'TelegramNotifier', return_value=failing):
            with self.assertRaises(Exception):
                mhn.main()

        recovered, state = self._run(is_open=True)  # next cron run, Telegram back
        recovered.send.assert_called_once()
        self.assertIn('open', recovered.send.call_args[0][0].lower())
        self.assertEqual(state, {'is_open': True})

    def test_clock_failure_leaves_state_untouched(self):
        """A failing /clock must not be mistaken for a market state. The
        cron log is full of transient Alpaca 500s on exactly this call."""
        with open(self.state_path, 'w') as f:
            json.dump({'is_open': False}, f)

        mock_client = MagicMock()
        mock_client.get_clock.side_effect = Exception('Failed to get clock: 500')
        with patch.object(mhn, 'AlpacaClient', return_value=mock_client),              patch.object(mhn, 'TelegramNotifier', return_value=MagicMock()):
            with self.assertRaises(Exception):
                mhn.main()

        with open(self.state_path) as f:
            self.assertEqual(json.load(f), {'is_open': False})


if __name__ == '__main__':
    unittest.main()
