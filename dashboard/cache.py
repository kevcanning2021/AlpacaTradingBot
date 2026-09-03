import inspect
import time
from typing import Callable, Dict, Tuple, Any

from starlette.concurrency import run_in_threadpool

_store: Dict[Tuple[str, str], Tuple[float, Any]] = {}


async def get_or_fetch(account_id: str, endpoint: str, ttl_seconds: float, fetch: Callable[[], Any]) -> Any:
    """A cache hit returns immediately with no thread hop. A miss runs fetch()
    off the event loop thread -- found live 2026-09-03: fetch() is ordinary
    blocking I/O (urllib with a 30s timeout, or a plain file read) called
    directly from an async route handler, so one slow (not even erroring)
    account could stall this single-threaded server for every other request:
    other bots' cards, login, the issues panel, everything.

    fetch is usually a plain sync callable, run via run_in_threadpool. It can
    also itself be (or return) a coroutine -- _health_for in app.py caches an
    async helper this way, reusing another cache entry rather than making a
    second API call -- so both shapes are handled here rather than pushing
    that distinction onto every caller."""
    key = (account_id, endpoint)
    cached = _store.get(key)
    now = time.time()
    if cached and now - cached[0] < ttl_seconds:
        return cached[1]
    if inspect.iscoroutinefunction(fetch):
        data = await fetch()
    else:
        result = await run_in_threadpool(fetch)
        data = await result if inspect.isawaitable(result) else result
    _store[key] = (now, data)
    return data
