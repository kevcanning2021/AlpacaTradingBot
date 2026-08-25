from alpaca_client import AlpacaClient
from dashboard.readonly_client import ReadOnlyAlpacaClient
from dashboard import config

ACCOUNTS = {
    'prod': {
        'label': 'Production',
        'client': ReadOnlyAlpacaClient(AlpacaClient(
            api_key=config.ALPACA_PROD_API_KEY,
            secret_key=config.ALPACA_PROD_SECRET_KEY,
        )),
    },
    'test': {
        'label': 'Test ($100)',
        'client': ReadOnlyAlpacaClient(AlpacaClient(
            api_key=config.ALPACA_TEST_API_KEY,
            secret_key=config.ALPACA_TEST_SECRET_KEY,
        )),
    },
    'sofi': {
        'label': 'SOFI Bot',
        'client': ReadOnlyAlpacaClient(AlpacaClient(
            api_key=config.ALPACA_SOFI_API_KEY,
            secret_key=config.ALPACA_SOFI_SECRET_KEY,
        )),
    },
}


def get_client(account_id: str):
    account = ACCOUNTS.get(account_id)
    return account['client'] if account else None


# Static roster of every agent in the project, not just the three with a real
# Alpaca account above -- matches the plain-English naming scheme in the
# AlpacaTradingBot memory's reference_bot_naming.md, kept here (not
# hardcoded in the frontend) so it's one place to update. 'has_account'
# drives whether the dashboard shows live health/positions for that agent
# (via ACCOUNTS above / the research-agent decisions file) or just the
# static description -- Shadow Crypto is listed but not yet monitored,
# deliberately not omitted, per the phased dashboard-extension plan.
AGENTS_OVERVIEW = [
    {
        'id': 'prod',
        'label': 'Production',
        'role': 'Live paper trading, ~$100k account. Stock + crypto signals, real order execution.',
        'monitored': True,
    },
    {
        'id': 'test',
        'label': 'Test',
        'role': 'Live paper trading, $100 account. Trials new strategies before they’re promoted to Production.',
        'monitored': True,
    },
    {
        'id': 'sofi',
        'label': 'SOFI Bot',
        'role': 'Separate $50 account, SOFI-only, opening-range reversal strategy.',
        'monitored': True,
    },
    {
        'id': 'research_agent',
        'label': 'Research Agent',
        'role': 'Reviews buy signals on the Test account with live news search, can veto — never originates or executes a trade itself.',
        'monitored': True,
    },
    {
        'id': 'shadow_crypto',
        'label': 'Shadow Crypto',
        'role': 'Watches Binance BTC, analysis only — never places real orders.',
        'monitored': False,
    },
]