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
    SELL_RSI_MIN = 80  # Exit overbought positions. Was 85 (raised from 75 on 2026-07-09 against
                       # a ~90-day window, reasoning that RSI routinely sits >75 for weeks during
                       # a real rally). Lowered back to 80 on 2026-07-16 after a 14-month backtest
                       # (300 daily bars, full watchlist) showed 85 gives back more than it gains
                       # across typical/mixed conditions, not just the rare sustained rally the
                       # original change was tuned for: 65/85 = +0.634%/trade (31 trades, 35% win)
                       # vs. 65/80 = +1.286%/trade (32 trades, 47% win) over the same window.
                       # Verified not outlier-driven -- every leave-one-symbol-out result stayed
                       # positive (+0.50% to +1.74%/trade), unlike prior rejected hypotheses this
                       # same day (wider watchlist, entry confirmation) where excluding one symbol
                       # flipped the result negative. Both backtests (75->85 and 85->80) are
                       # honestly measuring different market windows/regimes, not contradictory --
                       # revisit if this account's real trade outcomes diverge from what this
                       # backtest predicts.

    # _compute_ema seeds each call's EMA from a plain average of the first `period`
    # bars *in whatever window it's given*, then smooths forward -- with too short a
    # window that seed barely has time to converge before the final value is used,
    # especially for EMA21 (k~=0.091, needs ~40-60 smoothing steps). 35 bars left
    # EMA21 undercooked; backtested (2026-07-16) against a continuous EMA over the
    # full history (the methodology behind every threshold in STRATEGY.md) and found
    # 75-90 bars is where the windowed version converges to match it -- confirmed
    # stable (identical results) from 75 bars on for stocks, 90 for crypto. Below
    # that, the 35-bar version was measurably weaker and outlier-driven. See
    # STRATEGY.md "Automated monitoring" for the full investigation.
    SIGNAL_BAR_WINDOW = 90

    def __init__(self, client):
        self.client = client

    def _analyze_bars(self, symbol: str, bars: List[Dict]) -> Dict:
        """Pure signal computation from already-fetched bars, kept separate from the
        batched fetch in scan() so signal logic and I/O don't get tangled together."""
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
        """Fetches all symbols' bars in as few batched API round trips as possible
        (one per asset class present in `watchlist`) instead of one call per symbol --
        same signals, same per-symbol error isolation, far fewer requests. Added
        2026-08-04; see get_bars_multi() docstring for the batching/fallback behavior."""
        stock_symbols = [s for s in watchlist if '/' not in s]
        crypto_symbols = [s for s in watchlist if '/' in s]

        bars_by_symbol: Dict[str, List[Dict]] = {}
        if stock_symbols:
            bars_by_symbol.update(self.client.get_bars_multi(stock_symbols, limit=self.SIGNAL_BAR_WINDOW))
        if crypto_symbols:
            bars_by_symbol.update(self.client.get_bars_multi(crypto_symbols, limit=self.SIGNAL_BAR_WINDOW))

        results = []
        for symbol in watchlist:
            try:
                result = self._analyze_bars(symbol, bars_by_symbol.get(symbol, []))
                logger.info(f"[SCAN] {symbol}: {result['signal'].upper()} — {result['reason']}")
                results.append(result)
            except Exception as e:
                logger.error(f"[SCAN] Error analyzing {symbol}: {e}")
                results.append({'symbol': symbol, 'signal': 'error', 'reason': str(e), 'price': 0.0})
        return results
