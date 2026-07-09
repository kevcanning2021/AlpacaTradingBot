import time
from typing import Callable, Dict, Tuple, Any

_store: Dict[Tuple[str, str], Tuple[float, Any]] = {}


def get_or_fetch(account_id: str, endpoint: str, ttl_seconds: float, fetch: Callable[[], Any]) -> Any:
    key = (account_id, endpoint)
    cached = _store.get(key)
    now = time.time()
    if cached and now - cached[0] < ttl_seconds:
        return cached[1]
    data = fetch()
    _store[key] = (now, data)
    return data
