import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Dict, List

SESSION_MAX_AGE_DAYS = 30
RATE_LIMIT_WINDOW_SECONDS = 900
RATE_LIMIT_MAX_ATTEMPTS = 10

_failed_attempts: Dict[str, List[float]] = {}


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
    payload = base64.urlsafe_b64encode(json.dumps({'iat': int(time.time())}).encode()).decode()
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


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
    return (time.time() - iat) < SESSION_MAX_AGE_DAYS * 86400


def check_rate_limit(ip: str) -> bool:
    """Returns True if this ip is still allowed to attempt a login."""
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _failed_attempts[ip] = attempts
    return len(attempts) < RATE_LIMIT_MAX_ATTEMPTS


def record_failed_attempt(ip: str) -> None:
    _failed_attempts.setdefault(ip, []).append(time.time())
