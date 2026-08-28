import logging
from typing import Dict, List

from config import settings

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


def _compute_bollinger(prices: List[float], period: int = 20, num_std: float = 2):
    """Rolling SMA +/- num_std*stdev, aligned to `prices` like _compute_ema
    (leading Nones for the first `period`-1 entries). Unlike EMA, a rolling
    SMA/stdev has no seed-convergence lag -- it only ever depends on the
    trailing `period` closes, so it's not sensitive to how many extra bars
    the caller fetched beyond that."""
    n = len(prices)
    mid = [None] * n
    upper = [None] * n
    lower = [None] * n
    for i in range(period - 1, n):
        window = prices[i - period + 1:i + 1]
        m = sum(window) / period
        var = sum((p - m) ** 2 for p in window) / period
        sd = var ** 0.5
        mid[i] = m
        upper[i] = m + num_std * sd
        lower[i] = m - num_std * sd
    return mid, upper, lower


class OpportunityScanner:
    """Scans a watchlist for entry signals and returns trade recommendations.
    Stocks: EMA9/21 crossover and Bollinger Band(20,2) mean-reversion run as two
    independent signal sources (see class docstring below and _analyze_bars) when
    settings.DUAL_SIGNAL_BOLLINGER_ENABLED is on; EMA-only otherwise. Crypto is
    always EMA9/21 -- ported from origin/master (see BOT_REGISTRY.md/2026-08-28)
    without its crypto Donchian-breakout replacement, which is out of scope here."""

    BUY_RSI_MAX = 65   # Don't buy into overbought conditions. Reverted from a local,
                       # never-backtested 70 (2026-08-28) back to the validated 65 --
                       # the dual-signal Bollinger addition below is the actual
                       # validated fix for low trade frequency, not a looser RSI gate.
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

    # Bollinger Band(20, 2) mean-reversion -- second stock signal source, ported
    # 2026-08-28 from origin/master (built there 2026-08-14 for a since-retired
    # sibling bot, never adopted by Main until now). Backtested against 22 months
    # of real daily bars (full walk-forward: entries, 5%/8% stops, reentry, $10/
    # 4-max/$50 test-account sizing): alone, 44 trades, 70.5% win rate, +1.66%/
    # trade, +16.67% total -- roughly a wash vs EMA's +17.37% on total return, but
    # wins far more often and roughly halves how often a 1-month rolling window is
    # a loser (19.1% vs 33.0%), at the cost of a fatter single-worst-month tail
    # (-7.09% vs -5.56%). Positive independently on both train and holdout halves,
    # not outlier-driven -- every leave-one-symbol-out result stayed solidly
    # positive. See STRATEGY.md. NEVER forward-tested live before this port --
    # gated by settings.DUAL_SIGNAL_BOLLINGER_ENABLED (default off) pending a real
    # paper-account track record.
    BOLLINGER_PERIOD = 20
    BOLLINGER_STD = 2
    BOLLINGER_OVERSOLD_RSI = 40  # entry confirmation: RSI must also say oversold

    # Dual stock signal sources: Bollinger and EMA9/21 run as two independent,
    # already-separately-validated entry sources on the stock watchlist (not one
    # replacing the other) -- more real trade frequency without loosening either
    # one's own bar (a grid search loosening Bollinger's own band/RSI strictness
    # found every looser variant traded about the same or more but with worse,
    # sometimes negative, expectancy -- that's why this is two signals, not one
    # loosened signal). Sharing the same MAX_POSITIONS/capital cap (not more risk,
    # more ways to find a real signal): backtested against the same 22 months, 90
    # trades (vs. 44 Bollinger-only / 50 EMA-only), +1.71%/trade (higher than
    # either alone, not diluted between them), +35.89% total return (vs. +16.67%
    # / +17.37%). Positive independently on train (+18.25%) and holdout (+15.70%),
    # leave-one-symbol-out robust (+1.20% to +2.19% everywhere).
    #
    # Crypto is explicitly NOT part of this dual setup and stays single-method
    # EMA9/21 -- a Bollinger mean-reversion variant was tested for crypto and was
    # badly negative (-34.56%/-50.88% over 9mo on BTC/ETH), so it's out of scope
    # here entirely, not just deferred.
    #
    # A held position's exit is governed by whichever method opened it (tracked
    # in trader.py: self.position_methods, persisted like peak_prices/
    # reentry_fired) -- a Bollinger entry exits on Bollinger's mid-band/
    # overbought rule, an EMA entry exits on EMA's crossunder/overbought rule,
    # never a mixed rule. See STRATEGY.md.
    #
    # The drought fallback that exists alongside this on origin/master (a
    # narrower band after 10+ flat trading days) was deliberately NOT ported --
    # validated on a single occurrence (n=1) there, thin evidence to stack on top
    # of what's already the first-ever live test of dual-signal Bollinger itself.
    # Candidate for a separate, later, independently-flagged follow-up once this
    # has real live results.

    def __init__(self, client):
        self.client = client

    def _analyze_bars(self, symbol: str, bars: List[Dict], held_method: str = None) -> Dict:
        """Pure signal computation from already-fetched bars, kept separate from the
        batched fetch in scan() so signal logic and I/O don't get tangled together.

        Crypto always uses EMA9/21 crossover, regardless of settings/held_method.

        For a stock, when settings.DUAL_SIGNAL_BOLLINGER_ENABLED is off, behavior is
        byte-identical to EMA-only (pre-dual-signal). When it's on: `held_method`
        (None if not currently held) decides what gets evaluated -- a held position
        is only ever checked against the ONE method that opened it (its own exit
        rule governs, not a mixed/either rule); a symbol with no open position is
        checked against BOTH methods for a fresh entry, taking whichever fires
        first (Bollinger preferred on same-day tie, matching the backtest's
        tie-break)."""
        if len(bars) < 22:
            return {'symbol': symbol, 'signal': 'hold', 'reason': 'insufficient history', 'price': 0.0}

        closes = [float(b['c']) for b in bars]
        price = closes[-1]
        rsi = _compute_rsi(closes)

        if '/' in symbol:
            return self._analyze_ema_crossover(symbol, closes, price, rsi)

        if not settings.DUAL_SIGNAL_BOLLINGER_ENABLED:
            return self._analyze_ema_crossover(symbol, closes, price, rsi)

        if held_method == 'ema':
            return self._analyze_ema_crossover(symbol, closes, price, rsi)
        if held_method == 'bollinger':
            return self._analyze_bollinger(symbol, closes, price, rsi)

        # Not currently held: check both for a fresh entry.
        bollinger_result = self._analyze_bollinger(symbol, closes, price, rsi)
        if bollinger_result['signal'] == 'buy':
            bollinger_result['method'] = 'bollinger'
            return bollinger_result
        ema_result = self._analyze_ema_crossover(symbol, closes, price, rsi)
        if ema_result['signal'] == 'buy':
            ema_result['method'] = 'ema'
            return ema_result
        return bollinger_result

    def _analyze_ema_crossover(self, symbol: str, closes: List[float], price: float, rsi: float) -> Dict:
        ema9 = _compute_ema(closes, 9)
        ema21 = _compute_ema(closes, 21)

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

    def _analyze_bollinger(self, symbol: str, closes: List[float], price: float, rsi: float) -> Dict:
        mid, upper, lower = _compute_bollinger(closes, self.BOLLINGER_PERIOD, self.BOLLINGER_STD)

        if len(lower) < 2 or lower[-1] is None or lower[-2] is None or mid[-1] is None:
            return {'symbol': symbol, 'signal': 'hold', 'reason': 'Bollinger calculation failed', 'price': price, 'rsi': rsi}

        prev_price = closes[-2]

        # Buy: bouncing back above the lower band after closing below it
        # (oversold reversion), RSI confirms momentum has actually turned.
        if prev_price < lower[-2] and price > lower[-1] and rsi < self.BOLLINGER_OVERSOLD_RSI:
            signal, reason = 'buy', f'Bollinger lower-band bounce (RSI {rsi:.1f})'
        # Sell: reverted to the mean (target hit) or overbought -- same
        # SELL_RSI_MIN overbought exit as the EMA crossover branch.
        elif price >= mid[-1] or rsi > self.SELL_RSI_MIN:
            signal = 'sell'
            reason = f'Reverted to middle band (RSI {rsi:.1f})' if price >= mid[-1] else f'Overbought RSI {rsi:.1f}'
        else:
            signal, reason = 'hold', f'No signal (RSI {rsi:.1f})'

        return {
            'symbol': symbol,
            'signal': signal,
            'reason': reason,
            'price': price,
            'rsi': round(rsi, 2),
            'bb_mid': round(mid[-1], 4),
            'bb_lower': round(lower[-1], 4),
            'bb_upper': round(upper[-1], 4) if upper[-1] is not None else None,
        }

    def scan(self, watchlist: List[str], held_methods: Dict[str, str] = None) -> List[Dict]:
        """Fetches all symbols' bars in as few batched API round trips as possible
        (one per asset class present in `watchlist`) instead of one call per symbol --
        same signals, same per-symbol error isolation, far fewer requests. Added
        2026-08-04; see get_bars_multi() docstring for the batching/fallback behavior.

        `held_methods`: {symbol: 'bollinger'|'ema'} for currently-held stock
        positions, so a held symbol is evaluated against the one method that
        opened it rather than either/both -- see _analyze_bars. Symbols not in
        this dict (or when it's None/empty) are treated as not currently held,
        i.e. checked for a fresh entry against both methods (when dual signal is
        enabled). Ignored entirely when settings.DUAL_SIGNAL_BOLLINGER_ENABLED is
        off, and harmless to pass in either case -- existing callers that don't
        pass it at all keep working unchanged."""
        held_methods = held_methods or {}
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
                result = self._analyze_bars(symbol, bars_by_symbol.get(symbol, []), held_methods.get(symbol))
                method_tag = f" [{result['method']}]" if 'method' in result else ''
                logger.info(f"[SCAN] {symbol}: {result['signal'].upper()}{method_tag} — {result['reason']}")
                results.append(result)
            except Exception as e:
                logger.error(f"[SCAN] Error analyzing {symbol}: {e}")
                results.append({'symbol': symbol, 'signal': 'error', 'reason': str(e), 'price': 0.0})
        return results
