import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Dict, List

SESSION_MAX_AGE_DAYS = 30
RATE_LIMIT_WINDOW_SECONDS = 900
RATE_LIMIT_MAX_ATTEMPTS = 10

_failed_attempts: Dict[str, List[float]] = {}

# Persisted (not just an in-memory global) so a service restart can't silently
# un-revoke every outstanding session -- found live 2026-09-03: logout only
# ever deleted the client-side cookie, so a captured copy of a session token
# (synced browser profile, disk/backup snapshot, XSS) stayed valid for up to
# SESSION_MAX_AGE_DAYS regardless of the user clicking logout.
_REVOCATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'session_revocation_state.json')


def hash_password(password: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split('$')
    except ValueError:
        return False
    if scheme != 'pbkdf2_sha256':
        return False
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(dk.hex(), hash_hex)


def sign_session(secret: str) -> str:
    # Sub-second precision (not int(time.time())) -- a session signed and then
    # revoked within the same whole second used to be indistinguishable, so
    # revoke_all_sessions() could fail to catch a session issued moments
    # earlier. Caught by test_auth.py's own tests, which sign and revoke only
    # 10ms apart.
    payload = base64.urlsafe_b64encode(json.dumps({'iat': time.time()}).encode()).decode()
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _min_valid_iat() -> int:
    try:
        with open(_REVOCATION_FILE) as f:
            return json.load(f).get('min_iat', 0)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0


def revoke_all_sessions() -> None:
    """Called on logout -- invalidates every session token issued before now,
    not just the one cookie being deleted client-side. This is a single-
    operator dashboard with no 'log out this device only' concept, and a
    stateless HMAC token has no per-session identifier to revoke
    individually anyway, so 'logout' means every outstanding session dies,
    which is the safer default if a cookie was ever captured elsewhere."""
    with open(_REVOCATION_FILE, 'w') as f:
        json.dump({'min_iat': time.time()}, f)


def verify_session(secret: str, cookie_value: str) -> bool:
    payload, _, sig = cookie_value.partition('.')
    if not sig:
        return False
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        iat = json.loads(base64.urlsafe_b64decode(payload))['iat']
    except Exception:
        return False
    if iat < _min_valid_iat():
        return False
    return (time.time() - iat) < SESSION_MAX_AGE_DAYS * 86400


def check_rate_limit(ip: str) -> bool:
    """Returns True if this ip is still allowed to attempt a login."""
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _failed_attempts[ip] = attempts
    return len(attempts) < RATE_LIMIT_MAX_ATTEMPTS


def record_failed_attempt(ip: str) -> None:
    _failed_attempts.setdefault(ip, []).append(time.time())
