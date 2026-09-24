"""Who hears about an alert: the user, or only the agent queue?

Standing instruction from the user, 2026-09-24: they are never to be the
fleet's error-reporting channel. Errors are the agent's to fix or to carry.
What reaches a phone is only what a human must decide or act on, and cannot
wait for the next sweep.

The trigger was ~48 identical Telegram messages a day from four stuck_veto
alerts, three of them for entirely CORRECT vetoes. The fleet was healthy and
the inbox said otherwise.
"""
import unittest
from datetime import datetime, timedelta, timezone

import watchdog


class IsAdvisoryTests(unittest.TestCase):
    """Advisory = announce once while unchanged, never urgent."""

    def test_review_grade_alerts_are_advisory(self):
        for key in ('log_errors:trading-2-0.service',
                    'git_drift:alpaca-bot',
                    'stale_code:nova-main.service',
                    'trading2:stuck_veto:SPY',
                    'production:stuck_veto:TSLA',
                    'trading2:api_error:orders',
                    'trading2:research_agent_read_error',
                    'sofi:not_configured'):
            self.assertTrue(watchdog.is_advisory(key), key)

    def test_a_normal_trade_is_advisory(self):
        """bot_order fires on EVERY order placed. With three bots trading,
        routing that to a phone is pure volume -- and it is not an error at
        all. Normal activity belongs on the dashboard."""
        self.assertTrue(watchdog.is_advisory('trading2:bot_order:abc123'))

    def test_a_broken_watchdog_check_is_the_agents_problem(self):
        self.assertTrue(watchdog.is_advisory('watchdog_internal_error:check_services'))

    def test_genuinely_urgent_classes_are_not_advisory(self):
        for key in ('service_down:nova-main.service',
                    'leaked_credential:sofi-bot',
                    'secrets_hygiene:alpaca-bot:tracked',
                    'prod:unattributed_order:xyz',
                    'prod:stop_loss_breach:META'):
            self.assertFalse(watchdog.is_advisory(key), key)


class AlertReachesUserTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def _ago(self, minutes):
        return (self.now - timedelta(minutes=minutes)).isoformat()

    def test_nothing_advisory_ever_reaches_the_user(self):
        for key in ('log_errors:x.service', 'git_drift:repo', 'stale_code:x.service',
                    'trading2:stuck_veto:SPY', 'trading2:bot_order:abc',
                    'trading2:api_error:orders',
                    'watchdog_internal_error:check_services'):
            self.assertFalse(
                watchdog.alert_reaches_user(key, first_seen=self._ago(600), now=self.now),
                f'{key} must never reach the user, even after hours')

    def test_a_freshly_downed_service_does_not_wake_the_user(self):
        """The sweep restarts it within ~15 min. Escalating on first detection
        would put an error in front of them that was about to fix itself --
        precisely what they asked to stop."""
        self.assertFalse(watchdog.alert_reaches_user(
            'service_down:nova-main.service', first_seen=self._ago(5), now=self.now))

    def test_a_service_still_down_after_the_grace_period_does(self):
        """If restarting had been going to work, it would have by now."""
        self.assertTrue(watchdog.alert_reaches_user(
            'service_down:nova-main.service', first_seen=self._ago(90), now=self.now))

    def test_the_boundary(self):
        g = watchdog.ESCALATE_AFTER_MINUTES
        self.assertFalse(watchdog.alert_reaches_user(
            'service_down:x', first_seen=self._ago(g - 1), now=self.now))
        self.assertTrue(watchdog.alert_reaches_user(
            'service_down:x', first_seen=self._ago(g + 1), now=self.now))

    def test_security_and_money_alerts_escalate_immediately(self):
        """No grace period: no amount of restarting addresses a leaked key,
        an order placed with someone else's credentials, or a breached stop."""
        for key in ('leaked_credential:sofi-bot',
                    'secrets_hygiene:alpaca-bot:tracked',
                    'prod:unattributed_order:xyz',
                    'prod:stop_loss_breach:META'):
            self.assertTrue(
                watchdog.alert_reaches_user(key, first_seen=self._ago(0), now=self.now), key)

    def test_missing_or_malformed_first_seen_does_not_escalate_or_crash(self):
        for bad in (None, '', 'not-a-timestamp'):
            self.assertFalse(watchdog.alert_reaches_user(
                'service_down:x', first_seen=bad, now=self.now))


if __name__ == '__main__':
    unittest.main()
