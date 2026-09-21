"""Regression tests for /api/accounts/{id}/trades, added 2026-09-21 --
realised P&L per completed round-trip, which the existing orders endpoint
structurally cannot show (an Alpaca order is one side, a buy OR a sell, and
carries no profit/loss of its own).

Covers both sources behind the one shape: Main/Sofi's append-ordered JSON
history, and Nova's sqlite journal.

Run with: python -m unittest tests.test_closed_trades -v
"""
import json
import os
import sqlite3
import tempfile
import unittest

from dashboard import app, config


class LoadClosedTradesJsonTests(unittest.TestCase):
    """Main/Sofi: a JSON list, oldest-first, already carrying pnl/pnl_pct."""

    def setUp(self):
        self._orig = dict(config.CLOSED_TRADES_PATHS)

    def tearDown(self):
        config.CLOSED_TRADES_PATHS.clear()
        config.CLOSED_TRADES_PATHS.update(self._orig)

    def _write(self, entries):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(entries, f)
            config.CLOSED_TRADES_PATHS['prod'] = f.name
            return f.name

    def test_returns_newest_first(self):
        """The file is append-order, but a reader wants the latest trade at
        the top -- same as every other recency-ordered panel."""
        self._write([
            {'symbol': 'OLD', 'pnl': 1.0, 'pnl_pct': 0.01, 'timestamp': '2026-01-01T00:00:00+00:00'},
            {'symbol': 'NEW', 'pnl': 2.0, 'pnl_pct': 0.02, 'timestamp': '2026-02-01T00:00:00+00:00'},
        ])
        out = app._load_closed_trades('prod')
        self.assertEqual([t['symbol'] for t in out], ['NEW', 'OLD'])

    def test_outcome_derived_from_pnl_sign(self):
        self._write([
            {'symbol': 'W', 'pnl': 5.0, 'pnl_pct': 0.05, 'timestamp': '2026-01-01T00:00:00+00:00'},
            {'symbol': 'L', 'pnl': -5.0, 'pnl_pct': -0.05, 'timestamp': '2026-01-02T00:00:00+00:00'},
        ])
        out = {t['symbol']: t['outcome'] for t in app._load_closed_trades('prod')}
        self.assertEqual(out, {'W': 'win', 'L': 'loss'})

    def test_pnl_r_is_none_for_main_and_sofi(self):
        """Main/Sofi don't size by risk multiple, so there is no R to report
        -- the frontend omits the R chip when this is null rather than
        inventing a number."""
        self._write([{'symbol': 'X', 'pnl': 1.0, 'pnl_pct': 0.01, 'timestamp': '2026-01-01T00:00:00+00:00'}])
        self.assertIsNone(app._load_closed_trades('prod')[0]['pnl_r'])

    def test_capped_at_max(self):
        self._write([
            {'symbol': f'S{i}', 'pnl': 1.0, 'pnl_pct': 0.01, 'timestamp': f'2026-01-{i+1:02d}T00:00:00+00:00'}
            for i in range(app.MAX_CLOSED_TRADES + 10)
        ])
        self.assertEqual(len(app._load_closed_trades('prod')), app.MAX_CLOSED_TRADES)

    def test_missing_file_is_empty_not_an_error(self):
        """A bot that hasn't closed a trade yet is a normal state."""
        config.CLOSED_TRADES_PATHS['prod'] = '/nonexistent/trade_history.json'
        self.assertEqual(app._load_closed_trades('prod'), [])

    def test_corrupt_file_is_empty_not_a_crash(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{not valid json')
            config.CLOSED_TRADES_PATHS['prod'] = f.name
        self.assertEqual(app._load_closed_trades('prod'), [])

    def test_unknown_account_returns_empty(self):
        self.assertEqual(app._load_closed_trades('nope'), [])


class LoadClosedTradesNovaTests(unittest.TestCase):
    """Nova: sqlite, has pnl_r but no stored percentage."""

    def setUp(self):
        self._orig = config.NOVA_JOURNAL_DB_PATH
        fd, self.db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE trades (symbol TEXT, exit_time TEXT, pnl_dollars REAL, '
                     'pnl_r REAL, outcome TEXT, quantity REAL, entry_price REAL)')
        conn.commit()
        conn.close()
        config.NOVA_JOURNAL_DB_PATH = self.db

    def tearDown(self):
        config.NOVA_JOURNAL_DB_PATH = self._orig
        os.unlink(self.db)

    def _insert(self, rows):
        conn = sqlite3.connect(self.db)
        conn.executemany('INSERT INTO trades VALUES (?,?,?,?,?,?,?)', rows)
        conn.commit()
        conn.close()

    def test_percentage_derived_from_cost_basis(self):
        """Nova stores no percentage, so it's derived as pnl/(qty*entry).
        Deliberately NOT (exit-entry)/entry, which inverts on a short."""
        self._insert([('AAPL', '2026-09-18T19:00:00+00:00', 1.0, 1.8, 'win', 2.0, 50.0)])
        t = app._load_closed_trades('trading2')[0]
        self.assertAlmostEqual(t['pnl_pct'], 0.01)  # 1.0 / (2*50)

    def test_r_multiple_is_reported(self):
        self._insert([('AAPL', '2026-09-18T19:00:00+00:00', 1.0, 1.82, 'win', 2.0, 50.0)])
        self.assertAlmostEqual(app._load_closed_trades('trading2')[0]['pnl_r'], 1.82)

    def test_newest_first_and_open_trades_excluded(self):
        self._insert([
            ('OLD', '2026-09-01T00:00:00+00:00', 1.0, 1.0, 'win', 1.0, 10.0),
            ('NEW', '2026-09-02T00:00:00+00:00', 1.0, 1.0, 'win', 1.0, 10.0),
            ('OPEN', None, None, None, None, 1.0, 10.0),
        ])
        self.assertEqual([t['symbol'] for t in app._load_closed_trades('trading2')], ['NEW', 'OLD'])

    def test_zero_cost_basis_does_not_divide_by_zero(self):
        """Defensive: a malformed row must not 500 the whole panel."""
        self._insert([('X', '2026-09-01T00:00:00+00:00', 1.0, 1.0, 'win', 0.0, 0.0)])
        self.assertIsNone(app._load_closed_trades('trading2')[0]['pnl_pct'])

    def test_missing_journal_is_empty_not_an_error(self):
        config.NOVA_JOURNAL_DB_PATH = '/nonexistent/journal.db'
        self.assertEqual(app._load_closed_trades('trading2'), [])


if __name__ == '__main__':
    unittest.main()
