"""check_stale_code: is the running process older than the code on disk?

The one question no other watchdog check can answer. Service-up, git-clean,
tests-pass and file-is-correct were ALL true on 2026-09-10 while three bots
ran days-old research agents, because Python caches imported modules and
nothing had restarted them.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import watchdog


class StaleCodeTests(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.join(os.path.dirname(__file__), '_stale_tmp')
        os.makedirs(self.repo, exist_ok=True)
        self.pyfile = os.path.join(self.repo, 'live_module.py')
        with open(self.pyfile, 'w') as f:
            f.write('x = 1')
        self._orig = dict(watchdog.SERVICE_REPOS)
        watchdog.SERVICE_REPOS.clear()
        watchdog.SERVICE_REPOS['fake.service'] = self.repo

    def tearDown(self):
        watchdog.SERVICE_REPOS.clear()
        watchdog.SERVICE_REPOS.update(self._orig)
        for root, dirs, files in os.walk(self.repo, topdown=False):
            for n in files:
                os.remove(os.path.join(root, n))
            for n in dirs:
                os.rmdir(os.path.join(root, n))
        os.rmdir(self.repo)

    def _set_mtime(self, path, dt):
        ts = dt.timestamp()
        os.utime(path, (ts, ts))

    def test_flags_a_process_older_than_its_code(self):
        """The actual failure being guarded against."""
        now = datetime.now(timezone.utc)
        self._set_mtime(self.pyfile, now - timedelta(hours=6))
        with patch.object(watchdog, '_service_started_at',
                          return_value=now - timedelta(hours=30)):
            issues = watchdog.check_stale_code(now=now)
        self.assertEqual(len(issues), 1)
        key, msg = issues[0]
        self.assertEqual(key, 'stale_code:fake.service')
        self.assertIn('systemctl restart', msg)

    def test_a_same_second_deploy_is_not_reported_as_stale(self):
        """systemd reports ActiveEnterTimestamp to the SECOND; mtimes carry
        sub-second precision. A pull-then-restart inside one second therefore
        reads backwards -- a file written at 15:40:03.293 looks NEWER than a
        process systemd records as starting at 15:40:03. Seen live on
        2026-09-24 with a 0.3s "gap", on a service that was perfectly current.

        The grace period did not catch it: that measured how long ago the FILE
        changed, not how far the file is ahead of the PROCESS, so an hour-old
        file 0.3s newer than the process passed both tests."""
        now = datetime.now(timezone.utc)
        started = now - timedelta(hours=1)
        self._set_mtime(self.pyfile, started + timedelta(milliseconds=300))
        with patch.object(watchdog, '_service_started_at', return_value=started):
            self.assertEqual(watchdog.check_stale_code(now=now), [])

    def test_a_gap_just_past_the_tolerance_still_alerts(self):
        """The tolerance must not swallow the real case. A file a few minutes
        ahead of its process is a genuine missed restart."""
        now = datetime.now(timezone.utc)
        started = now - timedelta(hours=2)
        self._set_mtime(self.pyfile,
                        started + timedelta(seconds=watchdog.STALE_CODE_MIN_GAP_SECONDS + 120))
        with patch.object(watchdog, '_service_started_at', return_value=started):
            self.assertEqual(len(watchdog.check_stale_code(now=now)), 1)

    def test_silent_when_the_process_is_newer_than_the_code(self):
        now = datetime.now(timezone.utc)
        self._set_mtime(self.pyfile, now - timedelta(hours=30))
        with patch.object(watchdog, '_service_started_at',
                          return_value=now - timedelta(hours=1)):
            self.assertEqual(watchdog.check_stale_code(now=now), [])

    def test_a_deploy_in_progress_is_not_an_alert(self):
        """Edit-then-restart legitimately leaves a brief window where files
        are newer than the process. Alerting there would fire on every normal
        deploy and train everyone to ignore this check."""
        now = datetime.now(timezone.utc)
        self._set_mtime(self.pyfile, now - timedelta(minutes=2))
        with patch.object(watchdog, '_service_started_at',
                          return_value=now - timedelta(hours=5)):
            self.assertEqual(watchdog.check_stale_code(now=now), [])

    def test_a_stopped_service_is_not_reported_as_stale(self):
        """check_services already owns 'it is not running'; reporting that
        twice under a misleading name helps nobody."""
        now = datetime.now(timezone.utc)
        self._set_mtime(self.pyfile, now - timedelta(hours=6))
        with patch.object(watchdog, '_service_started_at', return_value=None):
            self.assertEqual(watchdog.check_stale_code(now=now), [])

    def test_ignores_directories_the_service_never_imports(self):
        """A venv dependency or a test file changing must not demand a
        production restart."""
        now = datetime.now(timezone.utc)
        self._set_mtime(self.pyfile, now - timedelta(hours=30))
        for junk in ('venv', 'tests', '__pycache__'):
            d = os.path.join(self.repo, junk)
            os.makedirs(d, exist_ok=True)
            f = os.path.join(d, 'recent.py')
            with open(f, 'w') as fh:
                fh.write('y = 2')
            self._set_mtime(f, now - timedelta(minutes=1))
        with patch.object(watchdog, '_service_started_at',
                          return_value=now - timedelta(hours=1)):
            self.assertEqual(watchdog.check_stale_code(now=now), [])

    def test_returns_none_for_a_repo_with_no_python(self):
        os.remove(self.pyfile)
        self.assertIsNone(watchdog._newest_code_mtime(self.repo))


class SystemdTimestampParsingTests(unittest.TestCase):
    """The first version asked systemd for ActiveEnterTimestampUSec, which
    this build does not expose. It got an empty string, returned None, and
    check_stale_code then skipped every service while reporting zero issues
    -- a monitor whose failure mode is silent good news."""

    def _show(self, output):
        class R:
            stdout = output
            returncode = 0
        return R()

    def test_parses_the_real_systemd_format(self):
        with patch.object(watchdog.subprocess, 'run',
                          return_value=self._show('Thu 2026-09-24 11:17:57 UTC')):
            got = watchdog._service_started_at('x.service')
        self.assertEqual(got, datetime(2026, 9, 24, 11, 17, 57, tzinfo=timezone.utc))

    def test_empty_output_means_never_run(self):
        with patch.object(watchdog.subprocess, 'run', return_value=self._show('')):
            self.assertIsNone(watchdog._service_started_at('x.service'))

    def test_unparseable_output_is_none_not_a_crash(self):
        with patch.object(watchdog.subprocess, 'run',
                          return_value=self._show('n/a')):
            self.assertIsNone(watchdog._service_started_at('x.service'))


if __name__ == '__main__':
    unittest.main()
