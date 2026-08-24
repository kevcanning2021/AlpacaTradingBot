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

    # Bollinger Band(20, 2) mean-reversion -- stock signal source as of 2026-08-14,
    # replacing the EMA9/21 crossover above for stocks (crypto is separate, see
    # DONCHIAN_BREAKOUT_PERIOD below and _analyze_bars). Backtested against 22 months of real daily bars (full
    # walk-forward: entries, 5%/8% stops, reentry, $10/4-max/$50 test-account
    # sizing) after Kevin flagged that a "flat month" felt like the strategy not
    # working: EMA9/21 baseline's total return over that window is basically a
    # wash vs. this (+17.37% vs +16.67%), but Bollinger wins far more often
    # (70.5% vs 48.0% win rate) and roughly halves how often a 1-month rolling
    # window is a loser (19.1% vs 33.0%), at the cost of a fatter single-worst-
    # month tail (-7.09% vs -5.56%). Positive independently on both train and
    # holdout halves (unlike a same-day MACD(12,26,9) variant, which lost on
    # train and was rejected as overfit) and not outlier-driven -- every
    # leave-one-symbol-out result stayed solidly positive. See STRATEGY.md.
    BOLLINGER_PERIOD = 20
    BOLLINGER_STD = 2
    BOLLINGER_OVERSOLD_RSI = 40  # entry confirmation: RSI must also say oversold

    # Dual stock signal sources, 2026-08-17: Bollinger and EMA9/21 run as two
    # independent, already-separately-validated entry sources on the stock
    # watchlist (not one replacing the other) -- Kevin wanted more real trade
    # frequency; a grid search loosening Bollinger's own band/RSI strictness
    # found every looser variant traded about the same or more but with worse
    # (sometimes negative) expectancy, so the extra frequency couldn't come
    # from relaxing one strategy's bar. Running both in parallel instead,
    # sharing the same MAX_POSITIONS/capital cap (not more risk, more ways to
    # find a real signal): backtested against the same 22 months, 90 trades
    # (vs. 44 Bollinger-only / 50 EMA-only), +1.71%/trade (higher than either
    # alone, not diluted between them), +35.89% total return (vs. +16.67% /
    # +17.37%) -- more capital cycling through equally-good trades, not lower
    # quality ones. Positive independently on train (+18.25%) and holdout
    # (+15.70%), leave-one-symbol-out robust (+1.20% to +2.19% everywhere).
    # Crypto is NOT part of this dual setup -- always single-method (Donchian
    # breakout as of 2026-08-24, see DONCHIAN_BREAKOUT_PERIOD below), same
    # reasoning as the original Bollinger scoping (crypto's volatility regime
    # is different enough to need its own independent validation).
    # A position's exit is governed by whichever method opened it (tracked in
    # `trader.py: self.position_methods`, persisted like peak_prices/
    # reentry_fired) -- a Bollinger entry exits on Bollinger's mid-band/
    # overbought rule, an EMA entry exits on EMA's crossunder/overbought rule,
    # never a mixed rule. See STRATEGY.md.

    # Drought fallback, 2026-08-18: even dual, the account sits at zero open
    # stock positions ~19% of trading days (11 stretches/22mo, avg 8.1 days,
    # worst 34 days) -- inherent to two genuinely selective strategies, not a
    # bug. Kevin wanted a cap on the worst stretches specifically. Tested
    # loosening BOLLINGER_OVERSOLD_RSI during a drought first: fired ZERO
    # extra trades in 22mo at RSI<55 -- during a real drought no band touch
    # happens at all, so RSI was never the actual blocker. Narrowing the
    # band instead (BOLLINGER_STD_FALLBACK) does trigger, rarely: exactly 1
    # extra trade in 22mo (a real win, +3.07%), cut the worst drought from 34
    # to 20 days. Only ever checked once DROUGHT_TRADING_DAYS have passed
    # with zero stock positions open (`trader.py` tracks this) and still
    # requires an actual lower-band touch-and-bounce with the normal RSI
    # confirmation -- narrower band, not no band. **Honesty flag: this is
    # validated on a single occurrence (n=1) since it's built to fire rarely
    # by design** -- directional evidence, not the same confidence as
    # everything else on this page. Does nothing for an ordinary few-day gap,
    # only the genuine long tail.
    BOLLINGER_STD_FALLBACK = 1.5
    DROUGHT_TRADING_DAYS = 10  # trader.py approximates this in calendar days

    # Crypto signal source, replaced 2026-08-24: the original EMA9/21 crossover
    # (inherited from the stock strategy, never independently backtested for
    # crypto per STRATEGY.md) had been live for weeks with zero filled trades.
    # Backtested three real candidates against 3.6 years of real BTC/USD and
    # ETH/USD daily bars (full walk-forward + train/holdout + rolling-window +
    # recent-9-month checks, same rigor as the stock Bollinger validation):
    # plain Bollinger mean-reversion was badly negative recently on both
    # symbols (-34.56%/-50.88% over the last 9 months, repeatedly stopped out
    # buying dips in a real decline); a trend-filtered Bollinger variant fired
    # too rarely to trust (3-7 trades total); this Donchian breakout is the
    # only candidate positive on the full window, the train half, AND the
    # holdout half, independently for BOTH symbols -- the same "positive on
    # both halves" bar that qualified stock Bollinger for deployment. Its
    # rolling-window profile (51-58% of 90-day windows flat or negative) is
    # the normal signature of trend-following, not a red flag: a low win rate
    # (~40%) carried by a few large trending moves, not many small wins --
    # flagged explicitly to Kevin before deploying since it means real losing
    # stretches are expected, not a sign it's broken.
    DONCHIAN_BREAKOUT_PERIOD = 20  # buy: today's close is a new N-day high
    DONCHIAN_EXIT_PERIOD = 10      # sell: today's close is a new N-day low

    def __init__(self, client):
        self.client = client

    def _analyze_bars(self, symbol: str, bars: List[Dict], held_method: str = None,
                       in_drought: bool = False) -> Dict:
        """Pure signal computation from already-fetched bars, kept separate from the
        batched fetch in scan() so signal logic and I/O don't get tangled together.

        Crypto always uses Donchian breakout (see class docstring above for
        the 2026-08-24 replacement of the original untested EMA9/21).

        For a stock, `held_method` (None if not currently held) decides what gets
        evaluated: a held position is only ever checked against the ONE method
        that opened it (its own exit rule governs, not a mixed/either rule); a
        symbol with no open position is checked against BOTH methods for a fresh
        entry, taking whichever fires first (Bollinger preferred on same-day tie,
        matching the backtest's tie-break and today's pre-dual default).

        `in_drought` (only meaningful when held_method is None -- a held position
        never needs a fallback) enables the narrow-band drought fallback inside
        _analyze_bollinger once DROUGHT_TRADING_DAYS have passed with zero stock
        positions open; see BOLLINGER_STD_FALLBACK above for what it does and its
        n=1 backtest caveat.
        """
        if len(bars) < 22:
            return {'symbol': symbol, 'signal': 'hold', 'reason': 'insufficient history', 'price': 0.0}

        closes = [float(b['c']) for b in bars]
        price = closes[-1]
        rsi = _compute_rsi(closes)

        if '/' in symbol:
            return self._analyze_donchian_breakout(symbol, closes, price, rsi)

        if held_method == 'ema':
            return self._analyze_ema_crossover(symbol, closes, price, rsi)
        if held_method == 'bollinger':
            return self._analyze_bollinger(symbol, closes, price, rsi)

        # Not currently held: check both for a fresh entry.
        bollinger_result = self._analyze_bollinger(symbol, closes, price, rsi, in_drought=in_drought)
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

    def _analyze_donchian_breakout(self, symbol: str, closes: List[float], price: float, rsi: float) -> Dict:
        """Buy: today's close is a new DONCHIAN_BREAKOUT_PERIOD-day high (momentum
        continuation). Sell: today's close is a new DONCHIAN_EXIT_PERIOD-day low
        (asymmetric channel -- exits faster than it enters, same shape as the
        backtest). RSI is carried through for parity with the other analyzers'
        return shape but isn't part of this signal -- the backtest that validated
        this (see class docstring) never used an RSI filter for it, only the
        stop-loss/trailing-stop overlay already applied in trader.py.

        Close-based (not high/low-based) specifically to match what was
        backtested exactly -- a live/backtest mismatch here would repeat the
        same class of bug already caught once today (PDT15Rev's Candle-1
        wrong-day bug): whatever gets validated is what should run live,
        not a hand-varied "more standard" version of it.
        """
        period, exit_period = self.DONCHIAN_BREAKOUT_PERIOD, self.DONCHIAN_EXIT_PERIOD
        if len(closes) < period + 1:
            return {'symbol': symbol, 'signal': 'hold', 'reason': 'insufficient history for Donchian',
                    'price': price, 'rsi': rsi}

        recent_high = max(closes[-(period + 1):-1])
        if price >= recent_high:
            signal, reason = 'buy', f'Donchian {period}-day breakout (RSI {rsi:.1f})'
        elif len(closes) >= exit_period + 1 and price <= min(closes[-(exit_period + 1):-1]):
            signal, reason = 'sell', f'Donchian {exit_period}-day breakdown (RSI {rsi:.1f})'
        else:
            signal, reason = 'hold', f'No breakout signal (RSI {rsi:.1f})'

        return {
            'symbol': symbol,
            'signal': signal,
            'reason': reason,
            'price': price,
            'rsi': round(rsi, 2),
            'donchian_high': round(recent_high, 4),
        }

    def _narrow_band_bounce(self, closes: List[float], price: float, rsi: float) -> bool:
        """Drought-only fallback check: a lower-band touch-and-bounce using
        BOLLINGER_STD_FALLBACK (narrower than the normal BOLLINGER_STD), same
        RSI confirmation as the standard entry -- softer band, not no band, and
        not a looser RSI (that was tried and fired zero extra trades in 22mo of
        backtesting, see the class docstring's DROUGHT_TRADING_DAYS note)."""
        _, _, lower = _compute_bollinger(closes, self.BOLLINGER_PERIOD, self.BOLLINGER_STD_FALLBACK)
        if len(lower) < 2 or lower[-1] is None or lower[-2] is None:
            return False
        prev_price = closes[-2]
        return prev_price < lower[-2] and price > lower[-1] and rsi < self.BOLLINGER_OVERSOLD_RSI

    def _analyze_bollinger(self, symbol: str, closes: List[float], price: float, rsi: float,
                            in_drought: bool = False) -> Dict:
        mid, upper, lower = _compute_bollinger(closes, self.BOLLINGER_PERIOD, self.BOLLINGER_STD)

        if len(lower) < 2 or lower[-1] is None or lower[-2] is None or mid[-1] is None:
            return {'symbol': symbol, 'signal': 'hold', 'reason': 'Bollinger calculation failed', 'price': price, 'rsi': rsi}

        prev_price = closes[-2]

        # Buy: bouncing back above the lower band after closing below it
        # (oversold reversion), RSI confirms momentum has actually turned.
        if prev_price < lower[-2] and price > lower[-1] and rsi < self.BOLLINGER_OVERSOLD_RSI:
            signal, reason = 'buy', f'Bollinger lower-band bounce (RSI {rsi:.1f})'
        elif in_drought and self._narrow_band_bounce(closes, price, rsi):
            signal, reason = 'buy', f'Bollinger drought fallback: narrow-band bounce (RSI {rsi:.1f})'
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

    def scan(self, watchlist: List[str], held_methods: Dict[str, str] = None,
              in_drought: bool = False) -> List[Dict]:
        """Fetches all symbols' bars in as few batched API round trips as possible
        (one per asset class present in `watchlist`) instead of one call per symbol --
        same signals, same per-symbol error isolation, far fewer requests. Added
        2026-08-04; see get_bars_multi() docstring for the batching/fallback behavior.

        `held_methods`: {symbol: 'bollinger'|'ema'} for currently-held stock
        positions, so a held symbol is evaluated against the one method that
        opened it rather than either/both -- see _analyze_bars. Symbols not in
        this dict (or when it's None/empty) are treated as not currently held,
        i.e. checked for a fresh entry against both methods.

        `in_drought`: whether the stock watchlist has sat at zero open positions
        for DROUGHT_TRADING_DAYS+ (see class docstring) -- enables the narrow-band
        Bollinger fallback for symbols not currently held. Caller's responsibility
        to compute (trader.py does, from persisted state); always pass False for
        a crypto-only scan call, since the fallback was never validated for
        crypto's volatility."""
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
                result = self._analyze_bars(symbol, bars_by_symbol.get(symbol, []), held_methods.get(symbol),
                                             in_drought=in_drought)
                method_tag = f" [{result['method']}]" if 'method' in result else ''
                logger.info(f"[SCAN] {symbol}: {result['signal'].upper()}{method_tag} — {result['reason']}")
                results.append(result)
            except Exception as e:
                logger.error(f"[SCAN] Error analyzing {symbol}: {e}")
                results.append({'symbol': symbol, 'signal': 'error', 'reason': str(e), 'price': 0.0})
        return results
