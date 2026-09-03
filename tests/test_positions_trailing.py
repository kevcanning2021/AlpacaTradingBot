"""Regression tests for the trailing-stop/peak-price data added to
/api/accounts/<id>/positions, 2026-09-03 -- user asked to see each
position's trailing stop and whether it's moved. Main/Sofi track a peak
price and ratchet a trailing stop off it (trader.py: _handle_trailing_stop);
Nova has no such mechanism at all, so its positions carry a fixed
stop_price/target_price from its own sqlite journal instead. These tests
cover both shapes plus the "neither" case (a position with no tracked data
yet), so the endpoint never crashes on partial/missing state.

Run with: python -m unittest tests.test_positions_trailing -v
"""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dashboard import app, cache, config


def _fake_request(account_id):
    req = MagicMock()
    req.path_params = {'account_id': account_id}
    return req


class LoadPeakPricesTests(unittest.TestCase):
    def setUp(self):
        self._orig = dict(config.PEAK_PRICES_PATHS)

    def tearDown(self):
        config.PEAK_PRICES_PATHS.clear()
        config.PEAK_PRICES_PATHS.update(self._orig)

    def test_returns_parsed_json_when_file_exists(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'AAPL': 329.59}, f)
            path = f.name
        try:
            config.PEAK_PRICES_PATHS['prod'] = path
            self.assertEqual(app._load_peak_prices('prod'), {'AAPL': 329.59})
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty_dict_not_an_error(self):
        config.PEAK_PRICES_PATHS['prod'] = '/nonexistent/path/peak_prices_state.json'
        self.assertEqual(app._load_peak_prices('prod'), {})

    def test_account_with_no_configured_path_returns_empty_dict(self):
        """Nova has no PEAK_PRICES_PATHS entry -- it has no trailing-stop
        mechanism to have peak data for."""
        self.assertEqual(app._load_peak_prices('trading2'), {})


class LoadNovaOpenTradeStopsTests(unittest.TestCase):
    def setUp(self):
        self._orig = config.NOVA_JOURNAL_DB_PATH
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        config.NOVA_JOURNAL_DB_PATH = self._orig
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_journal(self, rows):
        db_path = os.path.join(self._tmpdir, 'journal.db')
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY, symbol TEXT, stop_price REAL, target_price REAL, outcome TEXT)""")
        conn.executemany("INSERT INTO trades (symbol, stop_price, target_price, outcome) VALUES (?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()
        config.NOVA_JOURNAL_DB_PATH = db_path

    def test_returns_stop_and_target_for_open_trades_only(self):
        self._make_journal([
            ('NVDA', 170.0, 185.0, 'open'),
            ('AAPL', 310.0, 330.0, 'win'),  # closed -- must not appear
        ])
        result = app._load_nova_open_trade_stops()
        self.assertEqual(result, {'NVDA': {'stop_price': 170.0, 'target_price': 185.0}})

    def test_missing_db_returns_empty_dict_not_an_error(self):
        config.NOVA_JOURNAL_DB_PATH = '/nonexistent/journal.db'
        self.assertEqual(app._load_nova_open_trade_stops(), {})


class AccountPositionsEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_peak_paths = dict(config.PEAK_PRICES_PATHS)
        self._orig_journal_path = config.NOVA_JOURNAL_DB_PATH
        cache._store.clear()

    def tearDown(self):
        config.PEAK_PRICES_PATHS.clear()
        config.PEAK_PRICES_PATHS.update(self._orig_peak_paths)
        config.NOVA_JOURNAL_DB_PATH = self._orig_journal_path
        cache._store.clear()

    async def test_main_position_gets_peak_and_trailing_stop_fields(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'AAPL': 340.0}, f)
            path = f.name
        try:
            config.PEAK_PRICES_PATHS['prod'] = path
            client = MagicMock()
            client.get_positions.return_value = [{
                'symbol': 'AAPL', 'qty': '3.15', 'avg_entry_price': '317.47',
                'current_price': '330.28', 'market_value': '1040.38',
                'unrealized_pl': '40.35', 'unrealized_plpc': '0.04',
                'asset_class': 'us_equity',
            }]
            with patch('dashboard.app.get_client', return_value=client):
                response = await app.account_positions(_fake_request('prod'))
            body = json.loads(response.body)
            self.assertEqual(len(body), 1)
            pos = body[0]
            self.assertEqual(pos['peak_price'], 340.0)
            # entry_stop = 317.47 * 0.95, trailing_stop = 340.0 * 0.92
            self.assertAlmostEqual(pos['entry_stop_price'], 317.47 * 0.95, places=2)
            self.assertAlmostEqual(pos['trailing_stop_price'], 340.0 * 0.92, places=2)
            # The trailing stop has ratcheted above the fixed entry stop --
            # this is what "moved up" looks like: it's now the tighter,
            # more-protective level of the two.
            self.assertGreater(pos['trailing_stop_price'], pos['entry_stop_price'])
        finally:
            os.unlink(path)

    async def test_crypto_position_uses_crypto_thresholds(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({'BTC/USD': 80000.0}, f)
            path = f.name
        try:
            config.PEAK_PRICES_PATHS['prod'] = path
            client = MagicMock()
            client.get_positions.return_value = [{
                'symbol': 'BTC/USD', 'qty': '0.001', 'avg_entry_price': '75000',
                'current_price': '79000', 'market_value': '79', 'unrealized_pl': '4',
                'unrealized_plpc': '0.05', 'asset_class': 'crypto',
            }]
            with patch('dashboard.app.get_client', return_value=client):
                response = await app.account_positions(_fake_request('prod'))
            pos = json.loads(response.body)[0]
            self.assertAlmostEqual(pos['entry_stop_price'], 75000 * (1 - config.CRYPTO_STOP_LOSS_THRESHOLD), places=2)
            self.assertAlmostEqual(pos['trailing_stop_price'], 80000.0 * (1 - config.CRYPTO_TRAILING_STOP_THRESHOLD), places=2)
        finally:
            os.unlink(path)

    async def test_nova_position_gets_fixed_stop_and_target_not_peak(self):
        tmpdir = tempfile.mkdtemp()
        try:
            db_path = os.path.join(tmpdir, 'journal.db')
            conn = sqlite3.connect(db_path)
            conn.execute("""CREATE TABLE trades (
                id INTEGER PRIMARY KEY, symbol TEXT, stop_price REAL, target_price REAL, outcome TEXT)""")
            conn.execute("INSERT INTO trades (symbol, stop_price, target_price, outcome) VALUES ('NVDA', 170.0, 185.0, 'open')")
            conn.commit()
            conn.close()
            config.NOVA_JOURNAL_DB_PATH = db_path

            client = MagicMock()
            client.get_positions.return_value = [{
                'symbol': 'NVDA', 'qty': '0.5', 'avg_entry_price': '178.0',
                'current_price': '180.0', 'market_value': '90', 'unrealized_pl': '1',
                'unrealized_plpc': '0.01', 'asset_class': 'us_equity',
            }]
            with patch('dashboard.app.get_client', return_value=client):
                response = await app.account_positions(_fake_request('trading2'))
            pos = json.loads(response.body)[0]
            self.assertEqual(pos['stop_price'], 170.0)
            self.assertEqual(pos['target_price'], 185.0)
            self.assertNotIn('peak_price', pos)
            self.assertNotIn('trailing_stop_price', pos)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def test_position_with_no_tracked_data_at_all_has_only_base_fields(self):
        """A position that just opened, before its bot's first check-cycle
        has run since -- must not crash, must not fabricate fields."""
        client = MagicMock()
        client.get_positions.return_value = [{
            'symbol': 'MSFT', 'qty': '1', 'avg_entry_price': '500',
            'current_price': '500', 'market_value': '500', 'unrealized_pl': '0',
            'unrealized_plpc': '0', 'asset_class': 'us_equity',
        }]
        with patch('dashboard.app.get_client', return_value=client):
            response = await app.account_positions(_fake_request('prod'))
        pos = json.loads(response.body)[0]
        self.assertNotIn('peak_price', pos)
        self.assertNotIn('stop_price', pos)
        self.assertEqual(pos['symbol'], 'MSFT')


if __name__ == '__main__':
    unittest.main()
