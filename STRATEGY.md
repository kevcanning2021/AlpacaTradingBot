# Strategy

## Goal

Iteratively refine this strategy based on real trade data to grow the
account — not a one-off build-and-leave project. Every threshold change
below is backed by a backtest against real historical bars, not intuition,
and every rejected idea is recorded so it doesn't get re-tried blind.

## What the bot actually does (current, live)

**Entry**: `scanner.py: OpportunityScanner._analyze` — buy when EMA9
crosses above EMA21 **and** RSI(14) < `BUY_RSI_MAX`.

**Exit**: sell when EMA9 crosses below EMA21, **or** RSI(14) >
`SELL_RSI_MIN`, regardless of crossover state.

RSI is a simple/Cutler's-style 14-period average of gains/losses
(`scanner.py: _compute_rsi`), not Wilder-smoothed. This is intentional —
every threshold below was backtested against this exact formula, so it's
internally consistent even though it won't match what a chart on
TradingView/ThinkorSwim shows for the same symbol.

Per open position, in order, each check (`trader.py: check_positions`):
1. **Stop-loss** (`_handle_stop_loss`) — entry-anchored, closes the
   position for real if P&L drops to/below the threshold.
2. **Trailing stop** (`_handle_trailing_stop`) — peak-anchored, closes if
   price pulls back from its tracked peak by the threshold. Independent of
   stop-loss; catches gains given back that an entry-anchored stop can't see.
3. **Re-entry** (`_handle_reentry`) — only reached if neither stop closed
   the position this check. Adds to the existing position (places a real
   buy, same notional sizing as a fresh entry) if price has pulled back
   from peak by the re-entry threshold **and** RSI has dropped back below
   `BUY_RSI_MAX` (added 2026-07-14 — a pullback alone doesn't confirm
   momentum has turned). Fires at most once per pullback episode.

Stock and crypto run the identical logic with separate, wider crypto
thresholds (crypto's ordinary volatility is much higher than stocks').
Stock checks are gated to NYSE market hours; crypto runs 24/7 on its own
schedule, `check_positions(asset_class=...)`-scoped so the two can never
touch each other's positions.

## Current thresholds and why

| Threshold | Value (stock / crypto) | Location | Last verified |
|---|---|---|---|
| `BUY_RSI_MAX` | 65 / 65 | `scanner.py` | 2026-07-16 backtest |
| `SELL_RSI_MIN` | 80 / 80 | `scanner.py` | 2026-07-16 backtest |
| Stop-loss | 5% / 15% | `config/settings.py` | 2026-07-13 (crypto) |
| Trailing stop | 8% / 20% | `config/settings.py` | 2026-07-13 (crypto) |
| Re-entry | 5% / 12.5% | `config/settings.py` | 2026-07-14 |

**`SELL_RSI_MIN` history**: started at 75, raised to 85 on 2026-07-09
(backtested against a ~90-day window — RSI was found to routinely sit >75
for weeks during a real rally, so 75 exited winners too early). Lowered to
80 on 2026-07-16 after a 14-month/300-bar backtest against the full
watchlist showed 85 gives back more than it gains across typical/mixed
conditions, not just the rare sustained rally the original change was
tuned for (65/85 = +0.634%/trade, 31 trades, 35% win vs. 65/80 =
+1.286%/trade, 32 trades, 47% win). Verified not outlier-driven — every
leave-one-symbol-out result stayed positive. **This is not a contradiction
of the 07-09 change** — the two backtests measured different market
windows/regimes honestly. Revisit if this account's real trade outcomes
start diverging from what the 2026-07-16 backtest predicted; that would be
a genuine signal the window doesn't represent current conditions.

**Stop-loss/trailing-stop split (stock vs. crypto)**: crypto's 8%
stock-tuned trailing stop would have tripped on ordinary volatility alone
in 50-58% of all possible 20-day holding windows (backtested against 100
real BTC/USD & ETH/USD daily bars, 2026-07-13). 20%/15% cuts that to
8-16% — comparable to what 8% achieved for stocks over the stock-tuned 5%.
Same "cut most, not all" philosophy, not zero false trips by design.

**Re-entry threshold (5% / 12.5%)**: crypto's value must stay strictly
below `CRYPTO_TRAILING_STOP_THRESHOLD` (20%), not equal to it —
`check_positions()` runs stop-loss → trailing-stop → re-entry in that
order and skips re-entry once a position is closed. Setting them equal (an
actual bug shipped and caught same-day, 2026-07-14) meant the trailing
stop always closed the position at the same pullback level before
re-entry could ever fire, making it dead code. 12.5% preserves the same
ratio as the stock config (5%/8% = 0.625, applied to crypto's 20%) rather
than a new guess.

## Rejected hypotheses — don't re-propose without new evidence

Tested 2026-07-16 against 300 real daily bars (~14 months), full
watchlist, using the exact live scanner logic:

- **Wider watchlist** (TSLA, AMD, COIN, PLTR, MSTR — liquid, higher-beta
  candidates). 4 of 5 net negative; combined -0.778%/trade vs. the current
  watchlist's ~+0.63-0.66%/trade over the same window. Higher beta bought
  more whipsaws, not more good trades, for this specific EMA9/21 crossover
  strategy — same failure mode as NVDA's known false trailing-stop trips.
- **Entry confirmation delay** (require the EMA9>EMA21 crossover to hold
  for N bars before entering, instead of firing immediately). Monotonically
  worse at every N tested: baseline +0.616%/trade → 2-bar -0.704% → 3-bar
  -0.863% → 4-bar -1.191%. This strategy already exits fast, so delaying
  entry just captures less of the early move for the same downside.
- **Intraday (1Hour) signal bars**, tested 2026-07-14. EMA9/RSI14 mean
  something completely different on hourly bars (~1.3-day vs. ~2-week
  lookback); naive swap and rescaled-period variants were both worse than
  daily bars, and the rescaled version's apparent edge was entirely one
  outlier symbol (GOOGL) — negative once excluded.

## Open items — real, deliberately not acted on

- **`adjust_strategy()` (`trader.py`) mutates `STOP_LOSS_THRESHOLD` and
  `REENTRY_THRESHOLD` at runtime** based on win/loss streak (3 consecutive)
  and equity-change-vs-`INITIAL_EQUITY`, with **no backtest behind this
  logic at all** — unlike every threshold above. It directly reassigns the
  module-level `settings.STOP_LOSS_THRESHOLD`, so a win/loss streak can
  silently override a carefully-verified threshold mid-session. Its
  trigger condition is further undermined by `win_streak`/`loss_streak`
  living only in memory (not persisted like `peak_prices`/`reentry_fired`
  are) — a restart resets the streak count, so behavior is inconsistent
  across deploys. Flagged 2026-07-16, not yet disabled or backtested —
  Kevin's call whether to keep, backtest, or remove it.
- **Wilder-smoothed vs. Cutler's-style RSI** — `_compute_rsi` is the
  latter; every threshold above was tuned against it, so it's internally
  consistent, but untested whether Wilder smoothing would perform
  differently. Needs a backtest before any change.
- **`win_streak`/`loss_streak` persistence** — not fixed alongside
  `peak_prices`/`reentry_fired`, specifically because it feeds
  `adjust_strategy()`'s threshold mutation above; persisting it changes
  what streak state means across a restart in a way that needs its own
  decision, not a mechanical copy of the peak-price fix.
- **Crypto's 60-min check interval** has no session-close bound the way
  stocks do — a stop-loss/trailing-stop breach between checks is caught
  late. Test account already has precedent for a tighter stock interval
  (5 min); same reasoning could apply to crypto, not yet done.
- **Shared buying-power pool between the stock and crypto scheduled
  jobs** (separate timers, each fetches its own fresh `get_account()`) —
  narrow remaining race if both fire in the same window; fails safe via
  Alpaca's own order validation (rejected order, not overdraft).

## How to backtest a change

Real historical bars are reachable from the VPS (and this dev machine),
not from the Strategy Review routine's cloud sandbox (network-blocked).
Pattern used for every backtest above: import `_compute_ema`/`_compute_rsi`
directly from `scanner.py` (or the whole `OpportunityScanner`), walk real
daily bars (`alpaca_client.py: get_bars`, paginates automatically now)
bar-by-bar simulating buy/sell, and **always check per-symbol and
leave-one-symbol-out** before trusting an aggregate — an aggregate that
only survives with one specific symbol included is that symbol's story,
not a real edge (see the rejected hypotheses above for two examples where
this check mattered).
