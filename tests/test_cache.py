"""Regression tests for dashboard/cache.py's get_or_fetch, added 2026-09-03
after a full fleet review found the blocking Alpaca client calls (urllib, 30s
timeout) were called directly from async Starlette route handlers -- one
genuinely slow (not even erroring) account could stall the single-threaded
event loop for every other request. get_or_fetch now runs a cache-miss fetch
via starlette.concurrency.run_in_threadpool, or awaits it directly if fetch
is itself async (used by app.py's _health_for, which caches a call to another
async helper).

No pytest/httpx in this venv -- unittest.IsolatedAsyncioTestCase needs no
extra dependency to test async code, matching the "mocked, direct calls, no
new deps" convention already used across the rest of this fleet's tests.

Run with: python -m unittest tests.test_cache -v
"""
import time
import unittest

from dashboard import cache


class GetOrFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._store.clear()

    async def test_sync_fetch_is_called_and_cached(self):
        calls = []

        def fetch():
            calls.append(1)
            return {'equity': '100'}

        result = await cache.get_or_fetch('acct1', 'summary', 60, fetch)
        self.assertEqual(result, {'equity': '100'})
        self.assertEqual(len(calls), 1)

    async def test_cache_hit_does_not_call_fetch_again(self):
        calls = []

        def fetch():
            calls.append(1)
            return {'equity': '100'}

        await cache.get_or_fetch('acct1', 'summary', 60, fetch)
        await cache.get_or_fetch('acct1', 'summary', 60, fetch)

        self.assertEqual(len(calls), 1)

    async def test_expired_ttl_calls_fetch_again(self):
        calls = []

        def fetch():
            calls.append(1)
            return {'equity': '100'}

        await cache.get_or_fetch('acct1', 'summary', 0.01, fetch)
        time.sleep(0.02)
        await cache.get_or_fetch('acct1', 'summary', 0.01, fetch)

        self.assertEqual(len(calls), 2)

    async def test_async_fetch_is_awaited_not_returned_as_a_coroutine(self):
        """The real bug this guards against: _health_for wraps a call to an
        async helper (_account_health) in get_or_fetch -- if that coroutine
        is returned unawaited, callers get a coroutine object instead of
        the actual health dict."""
        async def fetch():
            return {'healthy': True}

        result = await cache.get_or_fetch('acct1', 'health', 60, fetch)

        self.assertEqual(result, {'healthy': True})

    async def test_lambda_wrapping_an_async_call_is_also_awaited_correctly(self):
        """Matches app.py's actual call shape: `lambda: _account_health(id)`,
        a plain (non-async) callable whose invocation returns a coroutine."""
        async def _account_health():
            return {'healthy': True, 'detail': None}

        result = await cache.get_or_fetch('acct1', 'health', 60, lambda: _account_health())

        self.assertEqual(result, {'healthy': True, 'detail': None})

    async def test_different_endpoints_for_the_same_account_are_cached_separately(self):
        summary_calls = []
        positions_calls = []

        await cache.get_or_fetch('acct1', 'summary', 60, lambda: summary_calls.append(1) or {'a': 1})
        await cache.get_or_fetch('acct1', 'positions', 60, lambda: positions_calls.append(1) or [])
        await cache.get_or_fetch('acct1', 'summary', 60, lambda: summary_calls.append(1) or {'a': 1})

        self.assertEqual(len(summary_calls), 1)
        self.assertEqual(len(positions_calls), 1)


if __name__ == '__main__':
    unittest.main()
