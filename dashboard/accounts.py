from alpaca_client import AlpacaClient
from dashboard.readonly_client import ReadOnlyAlpacaClient
from dashboard import config

# Account nicknames established 2026-08-27 (see the fleet's BOT_REGISTRY.md /
# each bot's own '# NICKNAME:' .env comment) -- Main/Sofi/Nova here match
# those exactly so the dashboard and the registry never drift apart again.
ACCOUNTS = {
    'prod': {
        'label': 'Main',
        'client': ReadOnlyAlpacaClient(AlpacaClient(
            api_key=config.ALPACA_PROD_API_KEY,
            secret_key=config.ALPACA_PROD_SECRET_KEY,
        )),
    },
    # 'test' (Mini) intentionally removed 2026-08-27: Alpaca caps free accounts
    # at 3 paper accounts, so Mini was retired and its account reassigned to
    # Nova -- see 'trading2' below, which now points at that same account
    # under its correct current label. Keeping a 'test' entry here would just
    # duplicate Nova's data under the wrong name.
    'sofi': {
        'label': 'Sofi',
        'client': ReadOnlyAlpacaClient(AlpacaClient(
            api_key=config.ALPACA_SOFI_API_KEY,
            secret_key=config.ALPACA_SOFI_SECRET_KEY,
        )),
    },
    'trading2': {
        'label': 'Nova',
        'client': ReadOnlyAlpacaClient(AlpacaClient(
            api_key=config.ALPACA_TRADING2_API_KEY,
            secret_key=config.ALPACA_TRADING2_SECRET_KEY,
        )),
    },
}


def get_client(account_id: str):
    account = ACCOUNTS.get(account_id)
    return account['client'] if account else None


# Static roster of every agent in the project, not just the ones with a real
# Alpaca account above -- matches the plain-English naming scheme in the
# AlpacaTradingBot memory's reference_bot_naming.md, kept here (not
# hardcoded in the frontend) so it's one place to update. 'monitored'
# drives whether the dashboard shows live health/positions for that agent
# (via ACCOUNTS above / the research-agent decisions file) or just the
# static description.
#
# Mini and Watcher intentionally omitted (removed from the dashboard
# 2026-08-27, having sat here 'retired' since their actual retirement earlier
# the same day): Mini's account was reassigned to Nova (Alpaca caps free
# accounts at 3), Watcher's cron job was removed (its own backtest was a
# losing strategy and Main already live-trades the same signal type on BTC).
# Full history lives in BOT_REGISTRY.md on the VPS and this project's memory,
# not on the dashboard itself.
AGENTS_OVERVIEW = [
    {
        'id': 'prod',
        'label': 'Main',
        'role': 'The main bot, trading real strategy signals on its paper account.',
        'monitored': True,
    },
    {
        'id': 'sofi',
        'label': 'Sofi',
        'role': 'Its own small account, trades SOFI stock only.',
        'monitored': True,
    },
    {
        'id': 'trading2',
        'label': 'Nova',
        'role': "A separate bot with a different trading style, on its own account (Mini's former account, reassigned 2026-08-27).",
        'monitored': True,
    },
    {
        'id': 'research_agent',
        'label': 'Research Agent',
        'role': "Double-checks a candidate entry against the news (Claude + web search) before it's placed, and can veto it. Reinstated 2026-08-27 for Nova, extended to Main and Sofi on 2026-08-28 -- each bot runs its own independent copy.",
        'monitored': True,
    },
]
