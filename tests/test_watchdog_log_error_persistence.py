"""Regression tests for watchdog.py: log-error alert persistence, added
2026-09-21 after a week-long miss.

check_new_log_errors only ever inspected the journal since the previous run
(~15 min), and main() deletes any alert key not re-raised on the following
run -- so this alert class was transient by construction. Nova logged 51
failed crypto orders across five days (Sep 14/18/19/20/21) and every
snapshot read of active_alerts correctly reported zero: spread across ~768
watchdog runs the alert was live roughly 3-4% of the time. The Telegram
pings all fired, so the human watching a phone had strictly better
information than anything reading the state file, and had to be the one to
notice. An alert meant to be seen by something that polls must outlive the
instant that raised it.

Run with: python -m unittest tests.test_watchdog_log_error_persistence -v
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import watchdog


def _journal(lines):
    """Stub subprocess.run so no real journalctl is invoked."""
    class R:
        stdout = '\n'.join(lines)
    return patch.object(watchdog.subprocess, 'run', return_value=R())


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class LogErrorPersistenceTests(unittest.TestCase):
    def test_alert_survives_a_run_with_no_new_errors(self):
        """The core fix. An error at 12:00 must still be reported at 12:15
        when that window happens to be quiet -- previously the alert simply
        vanished, which is exactly how five days of failures stayed
        invisible to anything checking periodically."""
        log = {}
        with _journal(['2026-09-21 12:00 ERROR Order submission failed']):
            first = watchdog.check_new_log_errors('nova.service', None, error_log=log, now=NOW)
        self.assertTrue(any(k == 'log_errors:nova.service' for k, _ in first))

        with _journal([]):  # quiet 15-minute window
            second = watchdog.check_new_log_errors(
                'nova.service', None, error_log=log, now=NOW + timedelta(minutes=15))
        self.assertTrue(any(k == 'log_errors:nova.service' for k, _ in second),
                        'alert disappeared during a quiet window -- the original bug')

    def test_recurrence_count_grows(self):
        """The message must change as errors recur, both so a reader can see
        it is ongoing and so main() knows to re-ping (it suppresses repeat
        Telegram sends while the message is unchanged)."""
        log = {}
        msgs = []
        # Spaced as a fraction of the retention window, not a fixed number of
        # hours: this test is about the COUNT growing, and hard-coding hours
        # made it fail the moment LOG_ERROR_WINDOW_HOURS was tuned from 24 to
        # 2 -- a fixture breaking on a config change it does not test.
        # Ageing out has its own test below.
        step = timedelta(hours=watchdog.LOG_ERROR_WINDOW_HOURS / 6.0)
        for i in range(3):
            with _journal([f'ERROR failure {i}']):
                issues = watchdog.check_new_log_errors(
                    'nova.service', None, error_log=log, now=NOW + step * i)
            msgs.append(dict(issues)['log_errors:nova.service'])
        self.assertIn('1 run(s)', msgs[0])
        self.assertIn('3 run(s)', msgs[2])
        self.assertNotEqual(msgs[1], msgs[2])

    def test_alert_clears_once_errors_age_out_of_the_window(self):
        """Persistence must not mean permanence -- a fault that genuinely
        stopped should stop alerting, or the signal becomes noise."""
        log = {}
        with _journal(['ERROR transient blip']):
            watchdog.check_new_log_errors('nova.service', None, error_log=log, now=NOW)

        later = NOW + timedelta(hours=watchdog.LOG_ERROR_WINDOW_HOURS + 1)
        with _journal([]):
            issues = watchdog.check_new_log_errors('nova.service', None, error_log=log, now=later)
        self.assertEqual(issues, [])
        self.assertNotIn('nova.service', log, 'stale unit left behind in state')

    def test_latest_sample_is_kept_for_context(self):
        log = {}
        with _journal(['ERROR first problem']):
            watchdog.check_new_log_errors('nova.service', None, error_log=log, now=NOW)
        with _journal(['ERROR second different problem']):
            issues = watchdog.check_new_log_errors(
                'nova.service', None, error_log=log, now=NOW + timedelta(hours=1))
        self.assertIn('second different problem', dict(issues)['log_errors:nova.service'])

    def test_stateless_mode_keeps_original_behaviour(self):
        """error_log=None must behave exactly as before -- the
        leaked-credential scan in the same function relies on being callable
        without any persistent state."""
        with _journal(['ERROR something broke']):
            issues = watchdog.check_new_log_errors('nova.service', None)
        self.assertIn('log_errors:nova.service', dict(issues))
        with _journal([]):
            self.assertEqual(watchdog.check_new_log_errors('nova.service', None), [])

    def test_unparseable_timestamp_in_state_does_not_crash(self):
        """Defensive: a state file written by an older version, or corrupted,
        must not take the whole watchdog run down -- it is the thing that is
        supposed to notice problems."""
        log = {'nova.service': {'events': ['not-a-timestamp'], 'latest_sample': 'x'}}
        with _journal([]):
            issues = watchdog.check_new_log_errors('nova.service', None, error_log=log, now=NOW)
        self.assertEqual(issues, [])


if __name__ == '__main__':
    unittest.main()
