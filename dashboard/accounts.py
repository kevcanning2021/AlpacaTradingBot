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
    'trading2': {
        'label': 'Trading 2.0',
        'client': ReadOnlyAlpacaClient(AlpacaClient(
            api_key=config.ALPACA_TRADING2_API_KEY,
            secret_key=config.ALPACA_TRADING2_SECRET_KEY,
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
        'role': 'The main bot, trading real strategy signals on its paper account.',
        'monitored': True,
    },
    {
        'id': 'test',
        'label': 'Test',
        'role': 'Small account used to try out new ideas before they go live on Production.',
        'monitored': True,
    },
    {
        'id': 'sofi',
        'label': 'SOFI Bot',
        'role': 'Its own small account, trades SOFI stock only.',
        'monitored': True,
    },
    {
        'id': 'trading2',
        'label': 'Trading 2.0',
        'role': 'A separate bot with a different trading style, on its own account.',
        'monitored': True,
    },
    {
        'id': 'research_agent',
        'label': 'Research Agent',
        'role': "Double-checks Test's buy signals against the news, and can block one it doesn't like. Never trades on its own.",
        'monitored': True,
    },
    {
        'id': 'shadow_crypto',
        'label': 'Shadow Crypto',
        'role': "Watches Bitcoin and takes notes, but isn't hooked up to the dashboard yet.",
        'monitored': False,
    },
]