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
        if timeframe == '1Day':
            lookback_days = limit * 3  # slack for weekends/holidays
        else:
            # Intraday bars only accrue during trading hours (crypto: 24/day, stocks:
            # ~7 hourly bars/day) -- limit*3 calendar days would compute a start date
            # years in the past for a few hundred bars, and get_bars doesn't paginate,
            # so the single API page would return stale data starting from that date
            # and never reach anything recent.
            bars_per_day = 24 if is_crypto else 7
            lookback_days = max(int(limit / bars_per_day * 2.5) + 5, 5)
        start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        if is_crypto:
            base_url = (
                f'{CRYPTO_DATA_BASE_URL}/bars'
                f'?symbols={quote(symbol, safe="")}&timeframe={timeframe}&start={start}&limit=10000&sort=asc'
            )
        else:
            base_url = (
                f'{DATA_BASE_URL}/stocks/{symbol}/bars'
                f'?timeframe={timeframe}&start={start}&limit=10000&feed=iex&sort=asc'
            )
        try:
            # Alpaca paginates bars regardless of the requested `limit` (observed:
            # ~200 hourly bars per page even with limit=10000) and returns a
            # next_page_token when more are available. Without following it, a
            # request needing more bars than one page holds would silently return
            # a stale chunk starting from `start` and never reach recent data.
            all_bars = []
            page_token = None
            for _ in range(50):  # safety cap against unexpected infinite pagination
                page_url = base_url + (f'&page_token={quote(page_token, safe="")}' if page_token else '')
                req = urllib.request.Request(page_url)
                req.add_header('APCA-API-KEY-ID', self.api_key)
                req.add_header('APCA-API-SECRET-KEY', self.secret_key)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                if is_crypto:
                    page_bars = (data.get('bars') or {}).get(symbol) or []
                else:
                    page_bars = data.get('bars') or []
                all_bars.extend(page_bars)
                page_token = data.get('next_page_token')
                if not page_token:
                    break
                # Bars come back oldest-first (sort=asc), so we can only stop early once
                # we're sure the *oldest* excess is what's being discarded, not the most
                # recent bars we actually want -- must paginate to completion.

            if all_bars and self._is_bar_still_forming(all_bars[-1], is_crypto, timeframe):
                all_bars = all_bars[:-1]
            return all_bars[-limit:]
        except Exception as e:
            logger.error(f"[get_bars] Failed to fetch bars for {symbol}: {e}")
            return []

    @staticmethod
    def _is_bar_still_forming(bar: Dict, is_crypto: bool = False, timeframe: str = '1Day') -> bool:
        """A bar dated today/this period isn't final until that period ends — until then
        its OHLC keeps shifting with the current price, which would make EMA/RSI
        crossover signals appear and disappear as the period progresses.

        Crypto trades 24/7 with no market close, so its daily bar rolls over at UTC
        midnight instead of the stock market's 4pm ET close. Intraday bars (e.g. '1Hour')
        are simple fixed-duration windows regardless of asset class, so they use an
        elapsed-time check instead of the daily market-close logic below.
        """
        bar_start = datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(timezone.utc)

        intraday_durations = {'1Min': timedelta(minutes=1), '5Min': timedelta(minutes=5),
                               '15Min': timedelta(minutes=15), '30Min': timedelta(minutes=30),
                               '1Hour': timedelta(hours=1)}
        if timeframe in intraday_durations:
            return bar_start + intraday_durations[timeframe] > datetime.now(timezone.utc)

        if is_crypto:
            bar_date = bar_start.date()
            return bar_date == datetime.now(timezone.utc).date()

        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        bar_date = bar_start.astimezone(tz).date()
        if bar_date != now.date():
            return False
        market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
        return now < market_close
