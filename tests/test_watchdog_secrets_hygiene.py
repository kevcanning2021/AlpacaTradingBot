"""Regression tests for watchdog.py's security checks, added 2026-09-05 after
rotating a real exposed Nova API key/secret (a dataclass repr in a test
failure printed both into a Claude session's own output). Built generally per
the user's explicit ask ("add that into a check to do and other security
checks wherever possible"), not just noted as a one-off:

- check_secrets_hygiene(): a fleet .env accidentally tracked by git, or with
  loose (non-600) permissions.
- check_new_log_errors()'s credential-shape scan: a real key/secret string
  appearing in a service's own log output.

Run with: python -m unittest tests.test_watchdog_secrets_hygiene -v
"""
import os
import shutil
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import watchdog


class SecretsHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.env_path = os.path.join(self.tmpdir, '.env')
        with open(self.env_path, 'w') as f:
            f.write('ALPACA_API_KEY=fake\n')
        os.chmod(self.env_path, stat.S_IRUSR | stat.S_IWUSR)  # 600

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_env_files(self, name='alpaca-bot', with_repo=True):
        env_files = {name: self.env_path}
        git_repos = {name: self.tmpdir} if with_repo else {}
        return patch('watchdog.ENV_FILES', env_files), patch('watchdog.GIT_REPOS', git_repos)

    def test_clean_env_file_is_not_flagged(self):
        """600 permissions, not git-tracked -- the actual current state of
        every real fleet .env as of 2026-09-05, confirmed live before this
        check was written."""
        p1, p2 = self._patch_env_files()
        with p1, p2, patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1)  # git ls-files: not tracked
            issues = watchdog.check_secrets_hygiene()
        self.assertEqual(issues, [])

    def test_loose_permissions_are_flagged(self):
        os.chmod(self.env_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)  # 644
        p1, p2 = self._patch_env_files()
        with p1, p2, patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            issues = watchdog.check_secrets_hygiene()
        perms_issues = [i for i in issues if 'permissions' in i[0]]
        self.assertEqual(len(perms_issues), 1)
        self.assertIn('644', perms_issues[0][1])

    def test_env_tracked_by_git_is_flagged(self):
        """The real risk this exists for: a .env swept into a careless
        `git add -A` would only surface once it reached GitHub -- this
        catches it on the VPS before that ever happens."""
        p1, p2 = self._patch_env_files()
        with p1, p2, patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)  # git ls-files: tracked
            issues = watchdog.check_secrets_hygiene()
        tracked_issues = [i for i in issues if 'tracked' in i[0]]
        self.assertEqual(len(tracked_issues), 1)

    def test_missing_env_file_is_skipped_not_flagged(self):
        env_files = {'alpaca-bot': os.path.join(self.tmpdir, 'does_not_exist.env')}
        with patch('watchdog.ENV_FILES', env_files):
            issues = watchdog.check_secrets_hygiene()
        self.assertEqual(issues, [])

    def test_repo_less_env_only_checks_permissions_not_git_tracking(self):
        """trading-2-0 has no git repo on this box (plain copied directory,
        see GIT_REPOS comment) -- must not attempt the git-tracked check for
        it at all, only permissions."""
        p1, p2 = self._patch_env_files(name='trading-2-0', with_repo=False)
        with p1, p2, patch('subprocess.run') as mock_run:
            issues = watchdog.check_secrets_hygiene()
        mock_run.assert_not_called()
        self.assertEqual(issues, [])


class LeakedCredentialLogScanTests(unittest.TestCase):
    @patch('subprocess.run')
    def test_alpaca_key_shape_in_log_is_flagged_without_leaking_the_value(self, mock_run):
        fake_key = 'PK' + 'A' * 26
        mock_run.return_value = MagicMock(returncode=0, stdout=f'some log line with {fake_key} in it\n')
        issues = watchdog.check_new_log_errors('sofi-bot.service', None)
        leaked = [i for i in issues if 'leaked_credential' in i[0]]
        self.assertEqual(len(leaked), 1)
        self.assertNotIn(fake_key, leaked[0][1])  # the whole point -- never repeat the secret into the alert

    @patch('subprocess.run')
    def test_secret_shape_near_the_word_secret_is_flagged(self, mock_run):
        fake_secret = 'B' * 44
        mock_run.return_value = MagicMock(returncode=0, stdout=f"AlpacaConfig(secret_key='{fake_secret}')\n")
        issues = watchdog.check_new_log_errors('trading-2-0.service', None)
        leaked = [i for i in issues if 'leaked_credential' in i[0]]
        self.assertEqual(len(leaked), 1)
        self.assertNotIn(fake_secret, leaked[0][1])

    @patch('subprocess.run')
    def test_a_44_char_alnum_string_with_no_secret_context_is_not_flagged(self, mock_run):
        """The secret pattern requires "secret" nearby -- a bare 44-char
        alnum string on its own (e.g. some unrelated hash or id) must not
        false-positive, or this would alert constantly."""
        mock_run.return_value = MagicMock(returncode=0, stdout='B' * 44 + '\n')
        issues = watchdog.check_new_log_errors('trading-2-0.service', None)
        self.assertEqual([i for i in issues if 'leaked_credential' in i[0]], [])

    @patch('subprocess.run')
    def test_ordinary_log_line_with_no_credential_shape_is_not_flagged(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='INFO Heartbeat: equity=$50.55, 1/11 symbols open (SPY)\n')
        issues = watchdog.check_new_log_errors('trading-2-0.service', None)
        self.assertEqual([i for i in issues if 'leaked_credential' in i[0]], [])


if __name__ == '__main__':
    unittest.main()
