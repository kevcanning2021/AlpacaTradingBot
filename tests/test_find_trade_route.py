"""The dashboard's only routes that can spend money.

Every other /api route is read-only. These start a systemd unit that places a
real order, so a session cookie alone is deliberately not enough: the password
is re-entered for this specific action, a double-click is rejected rather than
run twice, and the request is rate-limited like /login.

Two accounts carry the button (Main 2026-09-23, Sofi 2026-09-24) and one does
not. Everything below derives WHICH from config rather than naming accounts,
so pointing the button somewhere else cannot silently break the tests -- an
earlier version hardcoded 'sofi' in eleven places and broke the moment it
moved to Main.
"""
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dashboard import app, config

WITH_BUTTON = sorted(config.FIND_TRADE_UNITS)
WITHOUT_BUTTON = 'trading2'


def _req(account_id, body=None, ip='10.0.0.1'):
    r = MagicMock()
    r.path_params = {'account_id': account_id}
    r.client.host = ip

    async def _json():
        if body is None:
            raise ValueError("no body")
        return body
    r.json = _json
    return r


class ConfigShapeTests(unittest.TestCase):
    def test_both_accounts_have_a_unit_and_a_state_path(self):
        self.assertGreaterEqual(len(WITH_BUTTON), 2)
        for a in WITH_BUTTON:
            self.assertIsNotNone(config.find_trade_unit(a))
            self.assertIsNotNone(config.find_trade_state_path(a))

    def test_the_two_accounts_do_not_share_a_unit_or_a_state_file(self):
        """Sharing either would make one account's button report the other's
        result, or worse, trade the other's balance."""
        units = {config.find_trade_unit(a) for a in WITH_BUTTON}
        paths = {config.find_trade_state_path(a) for a in WITH_BUTTON}
        self.assertEqual(len(units), len(WITH_BUTTON))
        self.assertEqual(len(paths), len(WITH_BUTTON))

    def test_an_account_without_a_button_resolves_to_none(self):
        self.assertIsNone(config.find_trade_unit(WITHOUT_BUTTON))
        self.assertIsNone(config.find_trade_state_path(WITHOUT_BUTTON))


class FindTradeStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_never_run_reads_as_idle_on_every_button_account(self):
        for a in WITH_BUTTON:
            with patch.dict(config.FIND_TRADE_STATE_PATHS, {a: '/nonexistent/x.json'}), \
                 patch.object(app, '_find_trade_unit_active', return_value=False):
                r = await app.find_trade_status(_req(a))
            self.assertEqual(json.loads(r.body)['status'], 'idle', a)

    async def test_a_finished_run_is_reported_verbatim(self):
        a = WITH_BUTTON[0]
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'status': 'completed', 'traded': True, 'symbol': 'AAPL',
                       'message': 'Opened long AAPL'}, f)
            path = f.name
        with patch.dict(config.FIND_TRADE_STATE_PATHS, {a: path}), \
             patch.object(app, '_find_trade_unit_active', return_value=False):
            body = json.loads((await app.find_trade_status(_req(a))).body)
        self.assertEqual(body['symbol'], 'AAPL')
        self.assertFalse(body['running'])

    async def test_a_killed_run_is_not_left_running_forever(self):
        """A process killed mid-scan leaves status=running in the file for
        good, which would block every future press on idempotency. The unit's
        own state is the authority on whether it is still going."""
        a = WITH_BUTTON[0]
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'status': 'running', 'message': 'Scanning...'}, f)
            path = f.name
        with patch.dict(config.FIND_TRADE_STATE_PATHS, {a: path}), \
             patch.object(app, '_find_trade_unit_active', return_value=False):
            body = json.loads((await app.find_trade_status(_req(a))).body)
        self.assertEqual(body['status'], 'failed')
        self.assertIn('try again', body['message'].lower())

    async def test_an_account_without_a_button_gets_404(self):
        r = await app.find_trade_status(_req(WITHOUT_BUTTON))
        self.assertEqual(r.status_code, 404)


class FindTradeTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_account_without_a_button_starts_nothing(self):
        """Guards the worst outcome available here: trading the wrong balance
        because a path parameter was wrong."""
        with patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req(WITHOUT_BUTTON, {'password': 'x'}))
        self.assertEqual(r.status_code, 404)
        run.assert_not_called()

    async def test_a_wrong_password_starts_nothing(self):
        for a in WITH_BUTTON:
            with patch.object(app.auth, 'check_rate_limit', return_value=True), \
                 patch.object(app.auth, 'verify_password', return_value=False), \
                 patch.object(app.auth, 'record_failed_attempt'), \
                 patch.object(app.subprocess, 'run') as run:
                r = await app.find_trade_trigger(_req(a, {'password': 'wrong'}))
            self.assertEqual(r.status_code, 401, a)
            run.assert_not_called()

    async def test_a_missing_body_is_treated_as_a_missing_password(self):
        a = WITH_BUTTON[0]
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=False), \
             patch.object(app.auth, 'record_failed_attempt'), \
             patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req(a, body=None))
        self.assertEqual(r.status_code, 401)
        run.assert_not_called()

    async def test_rate_limiting_applies_before_the_password_is_checked(self):
        a = WITH_BUTTON[0]
        with patch.object(app.auth, 'check_rate_limit', return_value=False), \
             patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req(a, {'password': 'x'}))
        self.assertEqual(r.status_code, 429)
        run.assert_not_called()

    async def test_a_double_click_is_rejected_not_run_twice(self):
        a = WITH_BUTTON[0]
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=True), \
             patch.object(app, '_find_trade_unit_active', return_value=True), \
             patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req(a, {'password': 'ok'}))
        self.assertEqual(r.status_code, 409)
        run.assert_not_called()

    async def test_each_account_starts_its_OWN_unit(self):
        """The bug this guards: one shared unit would mean pressing Sofi's
        button traded Main's $100k."""
        for a in WITH_BUTTON:
            with patch.object(app.auth, 'check_rate_limit', return_value=True), \
                 patch.object(app.auth, 'verify_password', return_value=True), \
                 patch.object(app, '_find_trade_unit_active', return_value=False), \
                 patch.object(app.subprocess, 'run',
                              return_value=MagicMock(returncode=0, stderr='')) as run:
                r = await app.find_trade_trigger(_req(a, {'password': 'ok'}))
            self.assertEqual(json.loads(r.body)['ok'], True, a)
            self.assertEqual(
                run.call_args[0][0],
                ['sudo', '-n', 'systemctl', '--no-block', 'start',
                 config.find_trade_unit(a)],
                f"{a} started the wrong unit")

    async def test_a_failed_start_surfaces_as_an_error(self):
        a = WITH_BUTTON[0]
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=True), \
             patch.object(app, '_find_trade_unit_active', return_value=False), \
             patch.object(app.subprocess, 'run',
                          return_value=MagicMock(returncode=1, stderr='no sudo')):
            r = await app.find_trade_trigger(_req(a, {'password': 'ok'}))
        self.assertEqual(r.status_code, 502)


if __name__ == '__main__':
    unittest.main()
