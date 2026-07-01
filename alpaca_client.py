import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple
from config.settings import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, DATA_BASE_URL


class AlpacaClient:
    """Wrapper for Alpaca Trading API"""
    
    def __init__(self):
        self.api_key = ALPACA_API_KEY
        self.secret_key = ALPACA_SECRET_KEY
        self.base_url = ALPACA_BASE_URL
    
    def _request(self, method: str, path: str, payload: Optional[Dict] = None) -> Tuple[int, str]:
        """Make authenticated request to Alpaca API"""
        data = None if payload is None else json.dumps(payload).encode()
        url = self.base_url + path
        
        req = urllib.request.Request(url, method=method, data=data)
        req.add_header('APCA-API-KEY-ID', self.api_key)
        req.add_header('APCA-API-SECRET-KEY', self.secret_key)
        
        if payload is not None:
            req.add_header('Content-Type', 'application/json')
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except Exception as e:
            return None, str(e)
    
    def get_account(self) -> Dict:
        """Get account information"""
        status, body = self._request('GET', '/account')
        if status == 200:
            return json.loads(body)
        else:
            raise Exception(f"Failed to get account: {status} - {body}")
    
    def get_positions(self) -> List[Dict]:
        """Get all open positions"""
        status, body = self._request('GET', '/positions')
        if status == 200:
            return json.loads(body)
        else:
            return []
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get specific position by symbol"""
        status, body = self._request('GET', f'/positions/{symbol}')
        if status == 200:
            return json.loads(body)
        else:
            return None
    
    def create_order(self, symbol: str, qty: float, side: str, 
                    order_type: str = 'market', limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None, time_in_force: str = 'day') -> Dict:
        """Create a new order"""
        payload = {
            'symbol': symbol,
            'qty': qty,
            'side': side,
            'type': order_type,
            'time_in_force': time_in_force
        }
        
        if limit_price:
            payload['limit_price'] = limit_price
        if stop_price:
            payload['stop_price'] = stop_price
        
        status, body = self._request('POST', '/orders', payload)
        if status == 200 or status == 201:
            return json.loads(body)
        else:
            raise Exception(f"Failed to create order: {status} - {body}")
    
    def close_position(self, symbol: str, qty: Optional[float] = None) -> Dict:
        """Close a position"""
        path = f'/positions/{symbol}' + (f'?qty={qty}' if qty else '')
        status, body = self._request('DELETE', path)
        if status == 200 or status == 201:
            return json.loads(body)
        else:
            raise Exception(f"Failed to close position: {status} - {body}")
    
    def get_orders(self, status: str = 'all') -> List[Dict]:
        """Get all orders"""
        path = f'/orders?status={status}'
        status_code, body = self._request('GET', path)
        if status_code == 200:
            return json.loads(body)
        else:
            return []
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        status, body = self._request('DELETE', f'/orders/{order_id}')
        return status == 204

    def get_bars(self, symbol: str, timeframe: str = '1Day', limit: int = 35) -> List[Dict]:
        """Fetch OHLCV bars from the Alpaca data API."""
        url = (
            f'{DATA_BASE_URL}/stocks/{symbol}/bars'
            f'?timeframe={timeframe}&limit={limit}&feed=iex&sort=asc'
        )
        req = urllib.request.Request(url)
        req.add_header('APCA-API-KEY-ID', self.api_key)
        req.add_header('APCA-API-SECRET-KEY', self.secret_key)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                return data.get('bars', [])
        except Exception as e:
            return []
