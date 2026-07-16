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

## Automated monitoring: `strategy_check.py`

A VPS cron job (hourly, test account only, `/opt/alpaca-bot-test`), added
2026-07-16 in direct service of this doc's stated goal. Alert-only —
never trades, never edits thresholds — via the same WhatsApp/cooldown
pattern as `watchdog.py`:

- **Hourly**: re-runs `OpportunityScanner.scan()` against the live
  watchlist. Flags a data outage (too few bars / scanner error) or a SELL
  signal that's persisted 2+ consecutive checks on a symbol still held.
- **Once/day, after close**: re-backtests `BUY_RSI_MAX`/`SELL_RSI_MIN`
  (read live from `scanner.py`, so it can't drift from what's deployed)
  against a fresh 300-bar window, using the continuous-EMA methodology
  below. Flags a negative aggregate, an outlier-driven result
  (leave-one-symbol-out flips sign), or a >50% expectancy drop since the
  last run.

**Real finding while building this, 2026-07-16 — live/backtest EMA
methodology gap, unreconciled:** every backtest on this page (including
today's SELL_RSI_MIN change) computes one continuous EMA9/EMA21 series
over the whole fetched window. But `OpportunityScanner._analyze` — the
actual live code — calls `get_bars(symbol, limit=35)` fresh on every
single check and computes EMA9/21 from scratch on just those 35 bars each
time, with **no continuity between checks** (each call's EMA "seed" is a
plain average of whichever 9/21 bars happen to be first in that day's
35-bar window). Tested both against identical live data the same day:
continuous EMA reproduced the documented 65/80 result almost exactly (26
trades, +1.352%/trade, leave-one-out +0.71% to +1.89% — matches
"not outlier-driven"); the literal 35-bar-window-per-check version gave a
materially different, weaker, outlier-driven result (19 trades,
+0.914%/trade, flips negative excluding AAPL). `strategy_check.py`
deliberately uses the continuous version to stay comparable with this
page's history, **not** because it's confirmed to be what the live bot
does — it isn't. Whether this materially affects real trading (vs. being
a backtest-only artifact) is unverified. Kevin's call: investigate further
(e.g., quantify how often the two methodologies would have signaled
differently), change `scanner.py` to carry EMA state between checks
(a real behavior change, needs its own backtest-before-deploy per every
other threshold on this page), or leave as-is.

## Open items — real, deliberately not acted on

- **Live scanner's per-check EMA windowing doesn't match the backtest
  methodology that validated its own thresholds** — see "Automated
  monitoring" above for the full write-up. Not yet investigated further
  or fixed; changing `scanner.py`'s EMA calculation would be a real
  behavior change needing its own backtest first.
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

Real historical bars are reachable from the VPS and this dev machine, not
from the Strategy Review routine's cloud sandbox (persistently
network-blocked). An interactive Claude Code session can also reach
`data.alpaca.markets` through its own proxy tunnel, but that tunnel can
403 transiently — check `$HTTPS_PROXY/__agentproxy/status` and retry
before concluding it's blocked, rather than assuming the same persistent
block as the Strategy Review routine. Pattern used for every backtest
above: import `_compute_ema`/`_compute_rsi` directly from `scanner.py` (or
the whole `OpportunityScanner`), walk real daily bars (`alpaca_client.py:
get_bars`, paginates automatically now, returns plain dicts keyed
`c/h/l/n/o/t/v/vw` not objects) bar-by-bar simulating buy/sell, and
**always check per-symbol and leave-one-symbol-out** before trusting an
aggregate — an aggregate that only survives with one specific symbol
included is that symbol's story, not a real edge (see the rejected
hypotheses above for two examples where this check mattered).
