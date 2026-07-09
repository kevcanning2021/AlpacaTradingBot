from typing import Dict, List, Optional
from alpaca_client import AlpacaClient


class ReadOnlyAlpacaClient:
    """Exposes only read endpoints of AlpacaClient. No create_order/close_position/cancel_order
    methods exist on this wrapper, so the dashboard cannot place or cancel a trade even by mistake."""

    def __init__(self, client: AlpacaClient):
        self._client = client

    def get_account(self) -> Dict:
        return self._client.get_account()

    def get_positions(self) -> List[Dict]:
        return self._client.get_positions()

    def get_position(self, symbol: str) -> Optional[Dict]:
        return self._client.get_position(symbol)

    def get_orders(self, status: str = 'all', limit: int = 50) -> List[Dict]:
        return self._client.get_orders(status=status)[:limit]
