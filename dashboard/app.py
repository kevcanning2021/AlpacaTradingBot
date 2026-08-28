import json
import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from dashboard import auth, config
from dashboard.accounts import ACCOUNTS, AGENTS_OVERVIEW, get_client
from dashboard.cache import get_or_fetch

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / 'static'

SUMMARY_TTL = 10
POSITIONS_TTL = 10
ORDERS_TTL = 30
AGENTS_OVERVIEW_TTL = 10
RESEARCH_AGENT_DECISIONS_TTL = 10
RESEARCH_AGENT_DECISIONS_LIMIT = 50
ISSUES_TTL = 10


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
        # The real message (not just a generic "upstream fetch failed") matters here --
        # this is the same signal that caught Production's dead API key by hand earlier
        # (a 401 from Alpaca), now surfaced automatically via the agents-overview health
        # check below instead of requiring an SSH session to notice.
        logger.error(f"[dashboard] Failed to fetch summary for {account_id}: {e}")
        return JSONResponse({'error': str(e)}, status_code=502)
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


def _account_health(account_id: str):
    """True/error-message for one Alpaca-backed agent -- reuses the same
    cached get_account() fetch account_summary() already makes (same TTL,
    same cache key) so this costs no extra API call when both are hit in
    the same poll cycle."""
    client = get_client(account_id)
    if client is None:
        return {'healthy': False, 'detail': 'no account configured'}
    try:
        get_or_fetch(account_id, 'summary', SUMMARY_TTL, client.get_account)
        return {'healthy': True, 'detail': None}
    except Exception as e:
        return {'healthy': False, 'detail': str(e)}


def _load_research_decisions():
    """Reads all three bots' decision logs (Main, Sofi, Nova -- see
    config.RESEARCH_AGENT_DECISIONS_PATHS) and merges them into one flat,
    timestamp-sorted list, each entry tagged with which bot it came from.
    A missing file just means that bot hasn't logged a decision yet, not an
    error -- three independent processes, each writing its own file at its
    own pace."""
    flat = []
    for bot, path in config.RESEARCH_AGENT_DECISIONS_PATHS.items():
        try:
            with open(path) as f:
                decisions = json.load(f)
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError) as e:
            # One bot's file being unreadable (permissions, transient disk
            # issue) or corrupt shouldn't 502 the other two bots' decisions
            # -- log and skip just this source.
            logger.error(f"[dashboard] Failed to read {bot} research decisions ({path}): {e}")
            continue
        flat.extend(dict(d, symbol=symbol, bot=bot) for symbol, entries in decisions.items() for d in entries)
    flat.sort(key=lambda d: d.get('timestamp') or '', reverse=True)
    return flat


def _research_agent_health():
    """'Healthy' here means 'every bot's decisions file that exists is
    readable,' not 'the agent is currently active' -- a veto call only
    fires on a rare real buy signal (see agents/research_agent.py /
    bot/research_agent.py / pdt15rev-bot/research_agent.py), so a quiet
    file is normal, not a fault, unlike a regular heartbeat. detail carries
    the most recent decision's bot/timestamp/symbol across all three when
    available."""
    try:
        flat = _load_research_decisions()
    except (json.JSONDecodeError, OSError) as e:
        return {'healthy': False, 'detail': str(e)}
    if not flat:
        return {'healthy': True, 'detail': 'no decisions logged yet'}
    latest = flat[0]
    return {'healthy': True, 'detail': f"{len(flat)} logged, most recent: {latest['bot']}/{latest['symbol']} at {latest.get('timestamp', 'unknown time')}"}


async def agents_overview(request):
    def _health_for(agent):
        if not agent['monitored']:
            return {'healthy': None, 'detail': 'not yet monitored'}
        if agent['id'] == 'research_agent':
            return get_or_fetch('research_agent', 'health', AGENTS_OVERVIEW_TTL, _research_agent_health)
        return get_or_fetch(agent['id'], 'health', AGENTS_OVERVIEW_TTL, lambda: _account_health(agent['id']))

    return JSONResponse([dict(agent, health=_health_for(agent)) for agent in AGENTS_OVERVIEW])


async def research_agent_decisions(request):
    def _load():
        return _load_research_decisions()[:RESEARCH_AGENT_DECISIONS_LIMIT]

    try:
        data = get_or_fetch('research_agent', 'decisions', RESEARCH_AGENT_DECISIONS_TTL, _load)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[dashboard] Failed to read research agent decisions: {e}")
        return JSONResponse({'error': str(e)}, status_code=502)
    return JSONResponse(data)


def _load_active_alerts(path, source):
    """Shared shape: {active_alerts: {key: {first_seen, message}}}. Used for
    both watchdog.py's state (service down, new log errors, stop-loss
    breaches, unattributed orders) and the fleet review agent's state
    (medium/high-risk proposals awaiting approval, see /opt/fleet-review-agent,
    2026-08-28) -- two independently-scheduled writers, kept in separate files
    so they can never race on the same one, merged here into a single list."""
    try:
        with open(path) as f:
            state = json.load(f)
    except FileNotFoundError:
        return []
    active = state.get('active_alerts', {})
    return [
        {'key': key, 'message': v.get('message', ''), 'first_seen': v.get('first_seen'), 'source': source}
        for key, v in active.items() if isinstance(v, dict)
    ]


async def issues(request):
    """Surfaces currently-active issues from the fleet watchdog (service down,
    new log errors, stop-loss breaches, unattributed orders) and the fleet
    review agent (pending fix proposals awaiting approval) so they're visible
    on the dashboard itself, not just as a Telegram ping someone might miss.
    Reads each source's own state file rather than re-implementing any
    detection here -- one place per concern decides what counts as an issue,
    the dashboard just displays and merges. An empty/missing file is a normal
    'nothing wrong' state, not an error."""
    def _load():
        flat = (_load_active_alerts(config.WATCHDOG_STATE_PATH, 'watchdog')
                + _load_active_alerts(config.FLEET_REVIEW_STATE_PATH, 'fleet-review'))
        flat.sort(key=lambda i: i['first_seen'] or '', reverse=True)
        return flat

    try:
        data = get_or_fetch('watchdog', 'issues', ISSUES_TTL, _load)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[dashboard] Failed to read issue state: {e}")
        return JSONResponse({'error': str(e)}, status_code=502)
    return JSONResponse(data)


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
    Route('/api/agents-overview', agents_overview),
    Route('/api/research-agent/decisions', research_agent_decisions),
    Route('/api/issues', issues),
    Mount('/static', app=StaticFiles(directory=str(STATIC_DIR)), name='static'),
]

app = Starlette(routes=routes, middleware=[])
app.add_middleware(AuthMiddleware)
