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
}


def get_client(account_id: str):
    account = ACCOUNTS.get(account_id)
    return account['client'] if account else None
