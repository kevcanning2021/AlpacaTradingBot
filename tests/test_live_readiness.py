"""Tests for dashboard/live_readiness.py, added 2026-09-23.

The panel exists because "is it ready for real money yet?" was being
answered by judgement each time it was asked. These pin the parts of that
judgement that are easy to get subtly wrong -- particularly that UNKNOWN
must never collapse into PASS, since an unmeasured risk is not an absent
one and a readiness panel that overstates readiness is worse than none.

Run with: python -m unittest tests.test_live_readiness -v
"""
import json
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

    def _fill(self, n, pnl, overnight=True, span_days=90):
        """span_days defaults comfortably above MIN_SPAN_DAYS so tests about
        OTHER criteria are not incidentally failed by the span one. The PDT
        test overrides it downward, since day trades only breach the limit
        when they cluster inside a rolling window."""
        from datetime import datetime, timedelta, timezone
        base = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        conn = sqlite3.connect(self.db)
        rows = []
        for i in range(n):
            entry = base + timedelta(days=(span_days * i) // max(n - 1, 1))
            exit_ = entry + (timedelta(days=1) if overnight else timedelta(hours=5))
            rows.append((entry.isoformat(), exit_.isoformat(), pnl, 1.0, 100.0))
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
        r = lr.assess('trading2', config, bug_log_path=None)
        st = self._statuses(r)
        self.assertEqual(st['Sample size'], lr.FAIL)
        self.assertEqual(st['Positive expectancy'], lr.UNKNOWN)
        self.assertNotEqual(r['verdict'], 'ready')

    def test_unknowns_alone_give_unproven_never_ready(self):
        """No criterion failed, but something material is unmeasured --
        that is 'unproven', and must never read as ready."""
        self._fill(lr.MIN_TRADES, 1.0)
        r = lr.assess('trading2', config, bug_log_path=None)  # no repo -> stability unknown
        self.assertIn(lr.UNKNOWN, [c['status'] for c in r['criteria']])
        self.assertEqual(r['verdict'], 'unproven')

    def test_losing_bot_fails_expectancy(self):
        self._fill(lr.MIN_TRADES, -1.0)
        st = self._statuses(lr.assess('trading2', config, bug_log_path=None))
        self.assertEqual(st['Positive expectancy'], lr.FAIL)

    def test_heavy_day_trading_fails_pdt(self):
        """The blocker that is regulatory rather than performance-based:
        plenty of profitable trades can still be undeployable at small
        equity."""
        self._fill(60, 1.0, overnight=False, span_days=10)
        st = self._statuses(lr.assess('trading2', config, bug_log_path=None))
        self.assertEqual(st['PDT clearance'], lr.FAIL)

    def test_missing_journal_does_not_raise(self):
        config.NOVA_JOURNAL_DB_PATH = '/nonexistent/x.db'
        r = lr.assess('trading2', config, bug_log_path=None)
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



class SpanTests(unittest.TestCase):
    """Trade count alone treats 100 trades from five weeks and 100 from two
    years as equivalent evidence. They are not -- the first is essentially
    one market regime. This criterion exists to make the FAST bot's bar
    harder, which is the honest direction, since the fast bot is the one
    whose sample was being flattered."""

    def setUp(self):
        self._orig = config.NOVA_JOURNAL_DB_PATH
        fd, self.db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE trades (entry_time TEXT, exit_time TEXT, pnl_dollars REAL, '
                     'quantity REAL, entry_price REAL)')
        conn.commit()
        conn.close()
        config.NOVA_JOURNAL_DB_PATH = self.db

    def tearDown(self):
        config.NOVA_JOURNAL_DB_PATH = self._orig
        os.unlink(self.db)

    def _fill_over_days(self, n, span_days):
        from datetime import datetime, timedelta
        base = datetime(2026, 1, 1)
        conn = sqlite3.connect(self.db)
        rows = []
        for i in range(n):
            # spread evenly across the requested span
            d = base + timedelta(days=(span_days * i) // max(n - 1, 1))
            rows.append((d.isoformat(), (d + timedelta(hours=2)).isoformat(), 1.0, 1.0, 100.0))
        conn.executemany('INSERT INTO trades VALUES (?,?,?,?,?)', rows)
        conn.commit()
        conn.close()

    def _status(self, name):
        r = lr.assess('trading2', config, bug_log_path=None)
        return {c['name']: c['status'] for c in r['criteria']}[name]

    def test_many_trades_crammed_into_a_short_window_fails(self):
        """The case that motivated this: plenty of trades, but all from one
        market mood, so they are not independent evidence."""
        self._fill_over_days(200, span_days=20)
        self.assertEqual(self._status('Sample spans conditions'), lr.FAIL)

    def test_a_long_enough_span_passes(self):
        self._fill_over_days(200, span_days=lr.MIN_SPAN_DAYS + 10)
        self.assertEqual(self._status('Sample spans conditions'), lr.PASS)

    def test_span_is_independent_of_count(self):
        """Few trades over a long period still span the conditions -- the two
        criteria must fail for their own reasons, not each other's."""
        self._fill_over_days(4, span_days=lr.MIN_SPAN_DAYS + 30)
        r = lr.assess('trading2', config, bug_log_path=None)
        st = {c['name']: c['status'] for c in r['criteria']}
        self.assertEqual(st['Sample spans conditions'], lr.PASS)
        self.assertEqual(st['Sample size'], lr.FAIL)

    def test_single_trade_cannot_define_a_span(self):
        self._fill_over_days(1, span_days=0)
        self.assertEqual(self._status('Sample spans conditions'), lr.UNKNOWN)



class BugHistoryTests(unittest.TestCase):
    """The stability criterion previously grepped git for subjects starting
    with 'Fix' and was wrong in the dangerous direction -- most real fixes
    here do not say 'Fix', and --grep matches the body too, so Main showed
    12 days stable when it was 2 and Sofi showed 25. A readiness signal that
    flatters the bot is worse than no signal."""

    def _log(self, payload):
        fd, path = tempfile.mkstemp(suffix='.json')
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f)
        self.addCleanup(os.unlink, path)
        return path

    def test_uses_the_most_recent_entry_not_the_first(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
        path = self._log({'trading2': [{'date': '2020-01-01'}, {'date': recent}]})
        self.assertEqual(lr._days_since_last_bug('trading2', path), 3)

    def test_missing_bot_is_unknown_not_stable(self):
        """A bot with no recorded history must not read as having gone a long
        time without bugs -- absence of evidence is not evidence of absence."""
        path = self._log({'prod': [{'date': '2026-01-01'}]})
        self.assertIsNone(lr._days_since_last_bug('trading2', path))

    def test_missing_file_is_unknown(self):
        self.assertIsNone(lr._days_since_last_bug('trading2', '/nonexistent/bugs.json'))

    def test_malformed_entries_are_skipped_not_fatal(self):
        from datetime import datetime, timedelta, timezone
        good = (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()
        path = self._log({'trading2': [{'nope': 1}, {'date': 'not-a-date'}, {'date': good}]})
        self.assertEqual(lr._days_since_last_bug('trading2', path), 5)

    def test_all_entries_malformed_is_unknown(self):
        path = self._log({'trading2': [{'date': 'garbage'}]})
        self.assertIsNone(lr._days_since_last_bug('trading2', path))


if __name__ == '__main__':
    unittest.main()
