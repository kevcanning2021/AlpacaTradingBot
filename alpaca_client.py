import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
import pytz
from config.settings import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL, DATA_BASE_URL, CRYPTO_DATA_BASE_URL,
    TIMEZONE, MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE
)

logger = logging.getLogger(__name__)


def position_symbol(symbol: str) -> str:
    """Convert an order-form symbol to the form Alpaca uses on position objects
    and the /positions/{symbol} endpoints. Crypto orders use 'BTC/USD' but
    positions use 'BTCUSD' — a no-op for stock tickers, which have no slash."""
    return symbol.replace('/', '')


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
            raise Exception(f"Failed to get positions: {status} - {body}")
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get specific position by symbol"""
        status, body = self._request('GET', f'/positions/{symbol}')
        if status == 200:
            return json.loads(body)
        else:
            return None
    
    def create_order(self, symbol: str, qty: Optional[float] = None, side: str = 'buy',
                    order_type: str = 'market', limit_price: Optional[float] = None,
                    stop_price: Optional[float] = None, time_in_force: Optional[str] = None,
                    notional: Optional[float] = None, client_order_id: Optional[str] = None) -> Dict:
        """Create a new order. Provide either qty (shares) or notional (dollar amount, fractional-share buys).

        Crypto only accepts 'gtc' or 'ioc' for time_in_force (not 'day', which stocks default to),
        so an unspecified time_in_force defaults per asset class based on the symbol format.
        """
        if (qty is None) == (notional is None):
            raise ValueError("create_order requires exactly one of qty or notional")

        if time_in_force is None:
            time_in_force = 'gtc' if '/' in symbol else 'day'

        payload = {
            'symbol': symbol,
            'side': side,
            'type': order_type,
            'time_in_force': time_in_force
        }
        if notional is not None:
            payload['notional'] = notional
        else:
            payload['qty'] = qty
        if client_order_id is not None:
            payload['client_order_id'] = client_order_id

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
            raise Exception(f"Failed to get orders: {status_code} - {body}")
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        status, body = self._request('DELETE', f'/orders/{order_id}')
        return status == 204

    def get_bars(self, symbol: str, timeframe: str = '1Day', limit: int = 35) -> List[Dict]:
        """Fetch the most recent `limit` OHLCV bars from the Alpaca data API.

        Alpaca's bars endpoint returns `bars: null` when no `start` is given,
        so one is computed here (with slack for weekends/holidays) and the
        result is trimmed to the most recent `limit` bars in Python.

        Crypto symbols (containing '/', e.g. 'BTC/USD') are routed to the
        separate crypto data API, which lives at a different path and groups
        bars by symbol in the response instead of returning a flat list.
        """
        is_crypto = '/' in symbol
        start = (datetime.now(timezone.utc) - timedelta(days=limit * 3)).strftime('%Y-%m-%d')
        if is_crypto:
            url = (
                f'{CRYPTO_DATA_BASE_URL}/bars'
                f'?symbols={quote(symbol, safe="")}&timeframe={timeframe}&start={start}&limit=10000&sort=asc'
            )
        else:
            url = (
                f'{DATA_BASE_URL}/stocks/{symbol}/bars'
                f'?timeframe={timeframe}&start={start}&limit=10000&feed=iex&sort=asc'
            )
        req = urllib.request.Request(url)
        req.add_header('APCA-API-KEY-ID', self.api_key)
        req.add_header('APCA-API-SECRET-KEY', self.secret_key)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                if is_crypto:
                    bars = (data.get('bars') or {}).get(symbol) or []
                else:
                    bars = data.get('bars') or []
                if bars and self._is_bar_still_forming(bars[-1], is_crypto):
                    bars = bars[:-1]
                return bars[-limit:]
        except Exception as e:
            logger.error(f"[get_bars] Failed to fetch bars for {symbol}: {e}")
            return []

    @staticmethod
    def _is_bar_still_forming(bar: Dict, is_crypto: bool = False) -> bool:
        """A daily bar dated today isn't final until its trading day ends — until then its
        OHLC keeps shifting with the current price, which would make EMA/RSI crossover
        signals appear and disappear as the session progresses.

        Crypto trades 24/7 with no market close, so its daily bar rolls over at UTC
        midnight instead of the stock market's 4pm ET close.
        """
        if is_crypto:
            bar_date = datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(timezone.utc).date()
            return bar_date == datetime.now(timezone.utc).date()

        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        bar_date = datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(tz).date()
        if bar_date != now.date():
            return False
        market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
        return now < market_close
