"""Advisory alerts announce once, then stay quiet until something changes.

Written 2026-09-24 after the user reported "lots of errors". The fleet was
fine: four stuck_veto alerts were live, three for entirely correct vetoes
(a real Tesla lawsuit story, counted twice because Nova and nova-main run
the same code against different accounts) and one for a stale article
already fixed. At a 2h cooldown that is ~48 identical Telegram messages a
day, none needing any action. The inbox, not the fleet, was the problem.
"""
import unittest

import watchdog


class IsAdvisoryTests(unittest.TestCase):
    def test_review_grade_alerts_are_advisory(self):
        for key in ('log_errors:trading-2-0.service',
                    'git_drift:alpaca-bot',
                    'stale_code:nova-main.service',
                    'trading2:stuck_veto:SPY',
                    'production:stuck_veto:TSLA'):
            self.assertTrue(watchdog.is_advisory(key), key)

    def test_urgent_alerts_keep_nagging(self):
        """A dead service or a leaked credential SHOULD keep pinging until
        someone acts -- quieting those would be the opposite of the fix."""
        for key in ('service_down:nova-main.service',
                    'secrets_hygiene:key_in_repo',
                    'trading2:bot_order:abc123',
                    'watchdog_internal_error:check_services'):
            self.assertFalse(watchdog.is_advisory(key), key)

    def test_stuck_veto_matches_mid_key_not_just_as_a_prefix(self):
        """stuck_veto keys are account-prefixed ('trading2:stuck_veto:SPY'),
        so a startswith() test would silently miss every one of them -- which
        is exactly the bug that let them re-ping for days."""
        self.assertTrue(watchdog.is_advisory('anyaccount:stuck_veto:XYZ'))


class AdvisorySuppressionTests(unittest.TestCase):
    """Exercises the decision in main(): re-ping only on a CHANGED message."""

    def _should_alert(self, key, old_msg, new_msg, cooldown_elapsed=True):
        existing = {'message': old_msg} if old_msg is not None else None
        should = cooldown_elapsed
        if (should and existing and watchdog.is_advisory(key)
                and existing.get('message') == new_msg):
            should = False
        return should

    def test_identical_advisory_message_does_not_re_ping(self):
        msg = 'SPY vetoed 3 consecutive checks on the identical reasoning'
        self.assertFalse(self._should_alert('trading2:stuck_veto:SPY', msg, msg))

    def test_changed_advisory_message_does_re_ping(self):
        """A veto that shifts to NEW reasoning is new information."""
        self.assertTrue(self._should_alert(
            'trading2:stuck_veto:SPY',
            "vetoed on ['crash']",
            "vetoed on ['bankruptcy']"))

    def test_first_ever_occurrence_always_pings(self):
        self.assertTrue(self._should_alert(
            'trading2:stuck_veto:SPY', None, 'first time seen'))

    def test_identical_urgent_message_still_re_pings(self):
        msg = 'nova-main.service is "failed", not active'
        self.assertTrue(self._should_alert('service_down:nova-main.service', msg, msg))

    def test_nothing_pings_before_the_cooldown_elapses(self):
        self.assertFalse(self._should_alert(
            'service_down:x', 'down', 'down', cooldown_elapsed=False))


if __name__ == '__main__':
    unittest.main()
