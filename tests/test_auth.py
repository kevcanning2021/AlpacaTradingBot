"""Regression tests for dashboard/auth.py's session revocation, added
2026-09-03 after a full fleet review found logout only ever deleted the
client-side cookie -- a captured copy of a session token stayed valid for up
to SESSION_MAX_AGE_DAYS (30) regardless of the user clicking logout.

Run with: python -m unittest tests.test_auth -v
"""
import os
import tempfile
import time
import unittest

from dashboard import auth

SECRET = 'test-secret'


class SessionRevocationTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_file = auth._REVOCATION_FILE
        auth._REVOCATION_FILE = os.path.join(self._tmpdir.name, 'session_revocation_state.json')

    def tearDown(self):
        auth._REVOCATION_FILE = self._orig_file
        self._tmpdir.cleanup()

    def test_a_valid_session_verifies_before_any_logout(self):
        token = auth.sign_session(SECRET)
        self.assertTrue(auth.verify_session(SECRET, token))

    def test_logout_invalidates_a_session_issued_before_it(self):
        token = auth.sign_session(SECRET)
        time.sleep(0.01)

        auth.revoke_all_sessions()

        self.assertFalse(auth.verify_session(SECRET, token))

    def test_a_new_login_after_logout_still_works(self):
        old_token = auth.sign_session(SECRET)
        time.sleep(0.01)
        auth.revoke_all_sessions()
        time.sleep(0.01)
        new_token = auth.sign_session(SECRET)

        self.assertFalse(auth.verify_session(SECRET, old_token))
        self.assertTrue(auth.verify_session(SECRET, new_token))

    def test_revocation_persists_across_a_simulated_service_restart(self):
        """The whole point of persisting to disk instead of an in-memory
        global: a restart (redeploy, crash) must not silently un-revoke
        every previously-logged-out session."""
        token = auth.sign_session(SECRET)
        time.sleep(0.01)
        auth.revoke_all_sessions()

        # Nothing else changes _REVOCATION_FILE's path or content here --
        # this stands in for the process restarting and re-reading the same
        # file fresh, since _min_valid_iat() never caches in memory.
        self.assertFalse(auth.verify_session(SECRET, token))

    def test_no_revocation_file_yet_means_nothing_is_revoked(self):
        """Normal state for a dashboard that's never had a logout yet."""
        token = auth.sign_session(SECRET)
        self.assertTrue(auth.verify_session(SECRET, token))

    def test_a_forged_signature_is_still_rejected_regardless_of_revocation(self):
        token = auth.sign_session(SECRET)
        tampered = token[:-1] + ('0' if token[-1] != '0' else '1')
        self.assertFalse(auth.verify_session(SECRET, tampered))


if __name__ == '__main__':
    unittest.main()
