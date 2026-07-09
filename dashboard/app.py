import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from dashboard import auth, config
from dashboard.accounts import ACCOUNTS, get_client
from dashboard.cache import get_or_fetch

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / 'static'

SUMMARY_TTL = 10
POSITIONS_TTL = 10
ORDERS_TTL = 30


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith('/api/') and path != '/api/login':
            cookie = request.cookies.get('session', '')
            if not cookie or not auth.verify_session(config.DASHBOARD_SESSION_SECRET, cookie):
                return JSONResponse({'error': 'unauthorized'}, status_code=401)
        return await call_next(request)


async def login(request):
    ip = request.client.host if request.client else 'unknown'
    if not auth.check_rate_limit(ip):
        return JSONResponse({'error': 'too many attempts'}, status_code=429)

    body = await request.json()
    password = body.get('password', '')

    if not auth.verify_password(password, config.DASHBOARD_PASSWORD_HASH):
        auth.record_failed_attempt(ip)
        return JSONResponse({'error': 'invalid password'}, status_code=401)

    response = JSONResponse({'ok': True})
    response.set_cookie(
        'session',
        auth.sign_session(config.DASHBOARD_SESSION_SECRET),
        max_age=auth.SESSION_MAX_AGE_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite='strict',
    )
    return response


async def logout(request):
    response = JSONResponse({'ok': True})
    response.delete_cookie('session')
    return response


async def list_accounts(request):
    return JSONResponse([{'id': account_id, 'label': v['label']} for account_id, v in ACCOUNTS.items()])


def _account_or_404(account_id: str):
    client = get_client(account_id)
    if client is None:
        return None
    return client


async def account_summary(request):
    account_id = request.path_params['account_id']
    client = _account_or_404(account_id)
    if client is None:
        return JSONResponse({'error': 'unknown account'}, status_code=404)
    try:
        data = get_or_fetch(account_id, 'summary', SUMMARY_TTL, client.get_account)
    except Exception as e:
        logger.error(f"[dashboard] Failed to fetch summary for {account_id}: {e}")
        return JSONResponse({'error': 'upstream fetch failed'}, status_code=502)
    return JSONResponse({
        'equity': data.get('equity'),
        'cash': data.get('cash'),
        'buying_power': data.get('buying_power'),
        'portfolio_value': data.get('portfolio_value'),
        'status': data.get('status'),
        'trading_blocked': data.get('trading_blocked'),
    })


async def account_positions(request):
    account_id = request.path_params['account_id']
    client = _account_or_404(account_id)
    if client is None:
        return JSONResponse({'error': 'unknown account'}, status_code=404)
    try:
        data = get_or_fetch(account_id, 'positions', POSITIONS_TTL, client.get_positions)
    except Exception as e:
        logger.error(f"[dashboard] Failed to fetch positions for {account_id}: {e}")
        return JSONResponse({'error': 'upstream fetch failed'}, status_code=502)
    return JSONResponse([{
        'symbol': p.get('symbol'),
        'qty': p.get('qty'),
        'avg_entry_price': p.get('avg_entry_price'),
        'current_price': p.get('current_price'),
        'market_value': p.get('market_value'),
        'unrealized_pl': p.get('unrealized_pl'),
        'unrealized_plpc': p.get('unrealized_plpc'),
    } for p in data])


async def account_orders(request):
    account_id = request.path_params['account_id']
    client = _account_or_404(account_id)
    if client is None:
        return JSONResponse({'error': 'unknown account'}, status_code=404)
    try:
        data = get_or_fetch(account_id, 'orders', ORDERS_TTL, client.get_orders)
    except Exception as e:
        logger.error(f"[dashboard] Failed to fetch orders for {account_id}: {e}")
        return JSONResponse({'error': 'upstream fetch failed'}, status_code=502)
    return JSONResponse([{
        'id': o.get('id'),
        'symbol': o.get('symbol'),
        'side': o.get('side'),
        'qty': o.get('qty'),
        'notional': o.get('notional'),
        'type': o.get('type'),
        'status': o.get('status'),
        'filled_avg_price': o.get('filled_avg_price'),
        'submitted_at': o.get('submitted_at'),
        'filled_at': o.get('filled_at'),
    } for o in data])


async def index(request):
    return FileResponse(STATIC_DIR / 'index.html')


routes = [
    Route('/', index),
    Route('/api/login', login, methods=['POST']),
    Route('/api/logout', logout, methods=['POST']),
    Route('/api/accounts', list_accounts),
    Route('/api/accounts/{account_id}/summary', account_summary),
    Route('/api/accounts/{account_id}/positions', account_positions),
    Route('/api/accounts/{account_id}/orders', account_orders),
    Mount('/static', app=StaticFiles(directory=str(STATIC_DIR)), name='static'),
]

app = Starlette(routes=routes, middleware=[])
app.add_middleware(AuthMiddleware)
