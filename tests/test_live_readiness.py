"""Tests for dashboard/live_readiness.py, added 2026-09-23.

The panel exists because "is it ready for real money yet?" was being
answered by judgement each time it was asked. These pin the parts of that
judgement that are easy to get subtly wrong -- particularly that UNKNOWN
must never collapse into PASS, since an unmeasured risk is not an absent
one and a readiness panel that overstates readiness is worse than none.

Run with: python -m unittest tests.test_live_readiness -v
"""
import os
import sqlite3
import tempfile
import unittest

from dashboard import config, live_readiness as lr


class DrawdownTests(unittest.TestCase):
    def test_monotonic_gains_have_no_drawdown(self):
        self.assertEqual(lr._max_drawdown([0.01] * 10), 0.0)

    def test_peak_to_trough_is_measured_from_the_peak_not_the_start(self):
        """A run that gains then gives some back has drawn down from its
        high-water mark, even while still up overall -- measuring from the
        start would report zero and understate the pain of holding it."""
        dd = lr._max_drawdown([0.5, -0.2])
        self.assertAlmostEqual(dd, 0.2, places=6)

    def test_empty_history_is_zero_not_an_error(self):
        self.assertEqual(lr._max_drawdown([]), 0.0)


class PdtTests(unittest.TestCase):
    def _t(self, day, same_day=True):
        from datetime import datetime
        e = datetime(2026, 9, day, 10, 0)
        x = datetime(2026, 9, day if same_day else day + 1, 15, 0)
        return (e, x)

    def test_counts_only_same_day_round_trips(self):
        """A position held overnight is not a day trade and must not count
        toward the limit."""
        self.assertEqual(lr._pdt_exposure([self._t(1, same_day=False)] * 5), 0)

    def test_counts_day_trades_within_the_rolling_window(self):
        self.assertEqual(lr._pdt_exposure([self._t(1), self._t(2), self._t(3)]), 3)

    def test_no_trades_is_zero(self):
        self.assertEqual(lr._pdt_exposure([]), 0)


class AssessTests(unittest.TestCase):
    """End-to-end verdicts against a synthetic Nova-shaped journal."""

    def setUp(self):
        self._orig_db = config.NOVA_JOURNAL_DB_PATH
        fd, self.db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE trades (entry_time TEXT, exit_time TEXT, pnl_dollars REAL, '
                     'quantity REAL, entry_price REAL)')
        conn.commit()
        conn.close()
        config.NOVA_JOURNAL_DB_PATH = self.db

    def tearDown(self):
        config.NOVA_JOURNAL_DB_PATH = self._orig_db
        os.unlink(self.db)

    def _fill(self, n, pnl, overnight=True):
        conn = sqlite3.connect(self.db)
        rows = []
        for i in range(n):
            day = (i % 25) + 1
            entry = f'2026-09-{day:02d}T10:00:00+00:00'
            exit_ = f'2026-09-{(day if not overnight else day) :02d}T15:00:00+00:00' if not overnight \
                else f'2026-10-{day:02d}T15:00:00+00:00'
            rows.append((entry, exit_, pnl, 1.0, 100.0))
        conn.executemany('INSERT INTO trades VALUES (?,?,?,?,?)', rows)
        conn.commit()
        conn.close()

    def _statuses(self, result):
        return {c['name']: c['status'] for c in result['criteria']}

    def test_thin_history_reports_unknown_not_pass(self):
        """The failure that would make this panel actively harmful: a bot
        with almost no trades must not look acceptable just because nothing
        has gone wrong yet."""
        self._fill(5, 1.0)
        r = lr.assess('trading2', config, repo_path=None)
        st = self._statuses(r)
        self.assertEqual(st['Sample size'], lr.FAIL)
        self.assertEqual(st['Positive expectancy'], lr.UNKNOWN)
        self.assertNotEqual(r['verdict'], 'ready')

    def test_unknowns_alone_give_unproven_never_ready(self):
        """No criterion failed, but something material is unmeasured --
        that is 'unproven', and must never read as ready."""
        self._fill(lr.MIN_TRADES, 1.0)
        r = lr.assess('trading2', config, repo_path=None)  # no repo -> stability unknown
        self.assertIn(lr.UNKNOWN, [c['status'] for c in r['criteria']])
        self.assertEqual(r['verdict'], 'unproven')

    def test_losing_bot_fails_expectancy(self):
        self._fill(lr.MIN_TRADES, -1.0)
        st = self._statuses(lr.assess('trading2', config, repo_path=None))
        self.assertEqual(st['Positive expectancy'], lr.FAIL)

    def test_heavy_day_trading_fails_pdt(self):
        """The blocker that is regulatory rather than performance-based:
        plenty of profitable trades can still be undeployable at small
        equity."""
        self._fill(60, 1.0, overnight=False)
        st = self._statuses(lr.assess('trading2', config, repo_path=None))
        self.assertEqual(st['PDT clearance'], lr.FAIL)

    def test_missing_journal_does_not_raise(self):
        config.NOVA_JOURNAL_DB_PATH = '/nonexistent/x.db'
        r = lr.assess('trading2', config, repo_path=None)
        self.assertNotEqual(r['verdict'], 'ready')



class AccountScopedWrapperTests(unittest.TestCase):
    """_assess_readiness wraps assess() for one account. It must degrade to
    an explicit 'unknown' rather than raising: a panel saying "couldn't
    evaluate" is useful, a 502 that blanks it is not, and silently omitting
    the bot would read as though it had no criteria to meet."""

    def test_assessment_failure_returns_unknown_not_an_exception(self):
        from unittest.mock import patch
        from dashboard import app
        with patch.object(app.live_readiness, 'assess', side_effect=RuntimeError('boom')):
            r = app._assess_readiness('trading2')
        self.assertEqual(r['verdict'], 'unknown')
        self.assertEqual(r['criteria'], [])
        self.assertEqual(r['bot'], 'Nova')

    def test_result_is_labelled_with_the_bot_name(self):
        from dashboard import app
        for account_id, label in [('prod', 'Main'), ('sofi', 'Sofi'), ('trading2', 'Nova')]:
            self.assertEqual(app._assess_readiness(account_id)['bot'], label)


if __name__ == '__main__':
    unittest.main()
