"""A disabled unit that is not running is correct, not a fault.

Added 2026-09-24 after the fleet argued with its owner. The user ran
`systemctl disable --now sofi-bot` as a planned switchover; an automated
sweep read "inactive" as a crash and restarted it 78 seconds later, and this
check would have escalated the re-stopped unit to their phone 45 minutes on.
Two different pieces of automation overriding one decision the person had
already made.

`disabled` is the system's own record that a human retired something. Asking
it is self-maintaining, where the hand-kept exclusion list it replaces
(alpaca-bot-test, alpaca-telegram-bot, pdt15rev-bot) had to be remembered.
"""
import unittest
from unittest.mock import MagicMock, patch

import watchdog


class CheckServicesTests(unittest.TestCase):
    def _run(self, active_map, enabled_map):
        """Drive check_services with fabricated systemctl output."""
        units = list(active_map)
        active_out = MagicMock(stdout="\n".join(active_map[u] for u in units))

        def fake_run(cmd, *a, **kw):
            if cmd[1] == 'is-active':
                return active_out
            return MagicMock(stdout="\n".join(enabled_map.get(u, 'enabled') for u in units))

        with patch.object(watchdog, 'SERVICES', units), \
             patch.object(watchdog.subprocess, 'run', side_effect=fake_run):
            return watchdog.check_services()

    def test_everything_running_is_silent(self):
        self.assertEqual(self._run({'a.service': 'active'}, {'a.service': 'enabled'}), [])

    def test_an_enabled_unit_that_stopped_is_a_real_alert(self):
        """enabled + inactive is the genuine crash case -- someone meant this
        to be running and it is not."""
        issues = self._run({'a.service': 'inactive'}, {'a.service': 'enabled'})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0][0], 'service_down:a.service')

    def test_a_disabled_unit_that_stopped_is_not_an_alert(self):
        """The actual regression. Reporting this fights a decision instead of
        reporting a problem."""
        self.assertEqual(
            self._run({'sofi-bot.service': 'inactive'}, {'sofi-bot.service': 'disabled'}), [])

    def test_a_disabled_unit_that_failed_is_also_left_alone(self):
        """A retired unit crashing on its way out is still retired."""
        self.assertEqual(
            self._run({'old.service': 'failed'}, {'old.service': 'disabled'}), [])

    def test_disabled_and_enabled_units_are_judged_independently(self):
        issues = self._run(
            {'live.service': 'inactive', 'retired.service': 'inactive'},
            {'live.service': 'enabled', 'retired.service': 'disabled'})
        self.assertEqual([k for k, _ in issues], ['service_down:live.service'])

    def test_an_unreadable_enabled_state_reports_rather_than_stays_quiet(self):
        """If systemctl is-enabled cannot be read, assume nothing was retired.
        A false alert costs one message; a silent miss costs a stopped bot."""
        with patch.object(watchdog, 'SERVICES', ['a.service']), \
             patch.object(watchdog, '_enabled_states', return_value={}), \
             patch.object(watchdog.subprocess, 'run',
                          return_value=MagicMock(stdout='inactive')):
            issues = watchdog.check_services()
        self.assertEqual(len(issues), 1)


class EnabledStatesTests(unittest.TestCase):
    def test_states_are_paired_with_units_in_order(self):
        with patch.object(watchdog, 'SERVICES', ['a.service', 'b.service']), \
             patch.object(watchdog.subprocess, 'run',
                          return_value=MagicMock(stdout='enabled\ndisabled\n')):
            self.assertEqual(watchdog._enabled_states(),
                             {'a.service': 'enabled', 'b.service': 'disabled'})

    def test_a_subprocess_failure_returns_empty_not_an_exception(self):
        """check_services must survive this -- it is the outer call that
        actually matters, and it degrades to reporting."""
        with patch.object(watchdog, 'SERVICES', ['a.service']), \
             patch.object(watchdog.subprocess, 'run', side_effect=OSError("boom")):
            self.assertEqual(watchdog._enabled_states(), {})


if __name__ == '__main__':
    unittest.main()
