"""The dashboard's first route that can spend money.

Every other /api route is read-only. This one starts a systemd unit that
places a real order, so a session cookie alone is deliberately not enough:
the password is re-entered for this specific action, a double-click is
rejected rather than run twice, and the request is rate-limited like /login.
"""
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dashboard import app, config


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


class FindTradeStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_never_run_reads_as_idle_not_an_error(self):
        with patch.object(config, 'FIND_TRADE_STATE_PATH', '/nonexistent/x.json'), \
             patch.object(app, '_find_trade_unit_active', return_value=False):
            r = await app.find_trade_status(_req('sofi'))
        self.assertEqual(json.loads(r.body)['status'], 'idle')

    async def test_a_finished_run_is_reported_verbatim(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'status': 'completed', 'traded': True, 'symbol': 'AAPL',
                       'message': 'Opened long AAPL'}, f)
            path = f.name
        with patch.object(config, 'FIND_TRADE_STATE_PATH', path), \
             patch.object(app, '_find_trade_unit_active', return_value=False):
            body = json.loads((await app.find_trade_status(_req('sofi'))).body)
        self.assertEqual(body['symbol'], 'AAPL')
        self.assertTrue(body['traded'])
        self.assertFalse(body['running'])

    async def test_a_killed_run_is_not_left_running_forever(self):
        """A process killed mid-scan leaves status=running in the file for
        good, which would block every future press on idempotency. The unit's
        own state is the authority on whether it is still going."""
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'status': 'running', 'message': 'Scanning...'}, f)
            path = f.name
        with patch.object(config, 'FIND_TRADE_STATE_PATH', path), \
             patch.object(app, '_find_trade_unit_active', return_value=False):
            body = json.loads((await app.find_trade_status(_req('sofi'))).body)
        self.assertEqual(body['status'], 'failed')
        self.assertIn('try again', body['message'].lower())

    async def test_another_account_has_no_such_button(self):
        r = await app.find_trade_status(_req('prod'))
        self.assertEqual(r.status_code, 404)


class FindTradeTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_wrong_account_is_refused_before_anything_else(self):
        """Guards the worst outcome available here: trading the $100k balance
        because a path parameter was wrong."""
        with patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req('prod', {'password': 'x'}))
        self.assertEqual(r.status_code, 404)
        run.assert_not_called()

    async def test_a_wrong_password_starts_nothing(self):
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=False), \
             patch.object(app.auth, 'record_failed_attempt') as rec, \
             patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req('sofi', {'password': 'wrong'}))
        self.assertEqual(r.status_code, 401)
        run.assert_not_called()
        rec.assert_called_once()

    async def test_a_missing_body_is_treated_as_a_missing_password(self):
        """A bare POST with no JSON must not raise -- it must be refused."""
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=False), \
             patch.object(app.auth, 'record_failed_attempt'), \
             patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req('sofi', body=None))
        self.assertEqual(r.status_code, 401)
        run.assert_not_called()

    async def test_rate_limiting_applies_before_the_password_is_checked(self):
        with patch.object(app.auth, 'check_rate_limit', return_value=False), \
             patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req('sofi', {'password': 'x'}))
        self.assertEqual(r.status_code, 429)
        run.assert_not_called()

    async def test_a_double_click_is_rejected_not_run_twice(self):
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=True), \
             patch.object(app, '_find_trade_unit_active', return_value=True), \
             patch.object(app.subprocess, 'run') as run:
            r = await app.find_trade_trigger(_req('sofi', {'password': 'ok'}))
        self.assertEqual(r.status_code, 409)
        run.assert_not_called()

    async def test_a_valid_request_starts_exactly_the_scoped_unit(self):
        """sudoers allows one command. Anything else here would be refused at
        the OS boundary, but asserting it keeps the two in step."""
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=True), \
             patch.object(app, '_find_trade_unit_active', return_value=False), \
             patch.object(app.subprocess, 'run',
                          return_value=MagicMock(returncode=0, stderr='')) as run:
            r = await app.find_trade_trigger(_req('sofi', {'password': 'ok'}))
        self.assertEqual(json.loads(r.body)['ok'], True)
        self.assertEqual(run.call_args[0][0],
                         ['sudo', '-n', 'systemctl', 'start', config.FIND_TRADE_UNIT])

    async def test_a_failed_start_surfaces_as_an_error(self):
        with patch.object(app.auth, 'check_rate_limit', return_value=True), \
             patch.object(app.auth, 'verify_password', return_value=True), \
             patch.object(app, '_find_trade_unit_active', return_value=False), \
             patch.object(app.subprocess, 'run',
                          return_value=MagicMock(returncode=1, stderr='no sudo')):
            r = await app.find_trade_trigger(_req('sofi', {'password': 'ok'}))
        self.assertEqual(r.status_code, 502)


if __name__ == '__main__':
    unittest.main()
