import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def _compute_ema(prices: List[float], period: int) -> List[float]:
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def _compute_rsi(prices: List[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


class OpportunityScanner:
    """Scans a watchlist for EMA crossover + RSI signals and returns trade recommendations."""

    BUY_RSI_MAX = 65   # Don't buy into overbought conditions
    SELL_RSI_MIN = 75  # Exit overbought positions

    def __init__(self, client):
        self.client = client

    def _analyze(self, symbol: str) -> Dict:
        bars = self.client.get_bars(symbol, limit=35)
        if len(bars) < 22:
            return {'symbol': symbol, 'signal': 'hold', 'reason': 'insufficient history', 'price': 0.0}

        closes = [float(b['c']) for b in bars]
        price = closes[-1]

        ema9 = _compute_ema(closes, 9)
        ema21 = _compute_ema(closes, 21)
        rsi = _compute_rsi(closes)

        # Both arrays end at the same bar; compare last two values for crossover
        if len(ema9) < 2 or len(ema21) < 2:
            return {'symbol': symbol, 'signal': 'hold', 'reason': 'EMA calculation failed', 'price': price, 'rsi': rsi}

        prev_diff = ema9[-2] - ema21[-2]
        curr_diff = ema9[-1] - ema21[-1]

        if prev_diff < 0 and curr_diff > 0 and rsi < self.BUY_RSI_MAX:
            signal, reason = 'buy', f'EMA9 crossed above EMA21 (RSI {rsi:.1f})'
        elif (prev_diff > 0 and curr_diff < 0) or rsi > self.SELL_RSI_MIN:
            signal = 'sell'
            reason = (
                f'EMA9 crossed below EMA21 (RSI {rsi:.1f})'
                if prev_diff > 0 and curr_diff < 0
                else f'Overbought RSI {rsi:.1f}'
            )
        else:
            signal, reason = 'hold', f'No crossover signal (RSI {rsi:.1f})'

        return {
            'symbol': symbol,
            'signal': signal,
            'reason': reason,
            'price': price,
            'rsi': round(rsi, 2),
            'ema9': round(ema9[-1], 4),
            'ema21': round(ema21[-1], 4),
        }

    def scan(self, watchlist: List[str]) -> List[Dict]:
        results = []
        for symbol in watchlist:
            try:
                result = self._analyze(symbol)
                logger.info(f"[SCAN] {symbol}: {result['signal'].upper()} — {result['reason']}")
                results.append(result)
            except Exception as e:
                logger.error(f"[SCAN] Error analyzing {symbol}: {e}")
                results.append({'symbol': symbol, 'signal': 'error', 'reason': str(e), 'price': 0.0})
        return results
