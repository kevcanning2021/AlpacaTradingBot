# Strategy

## Goal

Iteratively refine this strategy based on real trade data to grow the
account — not a one-off build-and-leave project. Every threshold change
below is backed by a backtest against real historical bars, not intuition,
and every rejected idea is recorded so it doesn't get re-tried blind.

## What the bot actually does (current, live)

**Entry (crypto, and stocks when `DUAL_SIGNAL_BOLLINGER_ENABLED=false`)**:
`scanner.py: OpportunityScanner._analyze_ema_crossover` — buy when EMA9
crosses above EMA21 **and** RSI(14) < `BUY_RSI_MAX`.

**Entry (stocks, `DUAL_SIGNAL_BOLLINGER_ENABLED=true` — the live default
since 2026-09-03)**: `_analyze_bars` checks both signal sources for each
unheld symbol, Bollinger first, EMA second, first-fire-wins:
`_analyze_bollinger` (lower-band bounce: price closes below the 20-period,
2-std Bollinger lower band **and** RSI(14) < `BOLLINGER_OVERSOLD_RSI`) or
the EMA crossover above. Crypto always uses EMA only, flag or no flag — a
Bollinger variant was backtested for crypto and was badly negative (-34%
to -51% over 9mo on BTC/ETH), so it's deliberately not wired in there. A
held position is only re-evaluated by whichever method originally opened
it, tracked per-symbol in `position_method_state.json` (`trader.py`, same
load/save/pop-on-close pattern as `peak_prices`/`reentry_fired`).

**Exit**: sell when EMA9 crosses below EMA21, **or** RSI(14) >
`SELL_RSI_MIN`, regardless of crossover state. Applies the same way
regardless of which method opened the position — Bollinger has no
separate exit signal of its own.

**Research Agent veto**: every signal from either source, before it's
ever submitted as an order, passes through `agents/research_agent.py`'s
free news-based check (`RESEARCH_AGENT_VETO_ENABLED`, `trader.py:
scan_and_execute`) — a same `for sig in signals:` loop and veto check for
both methods, so a Bollinger-sourced buy is vetted exactly like an
EMA-sourced one, no separate call site to miss. Fails open (a failed
agent call never blocks a trade) and is logged either way, e.g. `[research_agent] IWM: veto=False (5 articles checked)`.

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
   momentum has turned) **and** the position has been open at least
   `MIN_REENTRY_AGE_HOURS` (added 2026-08-06 — a fresh position's peak is
   just its entry price, so an ordinary intraday dip minutes after a fill
   looked identical to a real pullback). Fires at most once per pullback
   episode.

Stock and crypto run the identical logic with separate, wider crypto
thresholds (crypto's ordinary volatility is much higher than stocks').
Stock checks are gated to NYSE market hours; crypto runs 24/7 on its own
schedule, `check_positions(asset_class=...)`-scoped so the two can never
touch each other's positions.

## Current thresholds and why

| Threshold | Value (stock / crypto) | Location | Last verified |
|---|---|---|---|
| `BUY_RSI_MAX` | 65 / 65 | `scanner.py` | 2026-07-16 (stock), 2026-07-17 (crypto, not contradicted) |
| `SELL_RSI_MIN` | 80 / 80 | `scanner.py` | 2026-07-16 (stock), 2026-07-17 (crypto, not contradicted) |
| `SIGNAL_BAR_WINDOW` | 90 / 90 | `scanner.py` | 2026-07-16 backtest |
| `BOLLINGER_PERIOD` | 20 (stock only) | `scanner.py` | see dual-signal note below |
| `BOLLINGER_STD` | 2 (stock only) | `scanner.py` | see dual-signal note below |
| `BOLLINGER_OVERSOLD_RSI` | 40 (stock only) | `scanner.py` | see dual-signal note below |
| Stop-loss | 5% / 15% | `config/settings.py` | 2026-07-13 (crypto) |
| Trailing stop | 8% / 20% | `config/settings.py` | 2026-07-13 (crypto) |
| Re-entry | 5% / 12.5% | `config/settings.py` | 2026-07-14 |
| Re-entry min age | 4h / 4h | `config/settings.py` | 2026-08-06 |

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

**`BUY_RSI_MAX`/`SELL_RSI_MIN` never had a crypto-specific backtest until
2026-07-17 — gap found by the Strategy Review routine, then closed.**
Unlike stop-loss/trailing-stop/re-entry above (each explicitly backtested
against real BTC/USD & ETH/USD bars), the RSI thresholds applied to crypto
by default inheritance — the 07-16 backtest that produced 65/80 only ever
walked the stock watchlist. Grid-searched `BUY_RSI_MAX` × `SELL_RSI_MIN`
against 300 real BTC/USD & ETH/USD daily bars (same continuous-EMA
methodology): current 65/80 is **not contradicted** — 6 trades,
+0.999%/trade, 50% win, and its leave-one-out is tight (BTC/USD +0.99%,
ETH/USD +1.00%, meaning both symbols agree with the aggregate). A couple
of other combos in the grid showed higher raw expectancy (65/70:
+2.172%/trade; 60/80: +3.030%/trade) but on thinner samples (6 and 4
trades) with much wider leave-one-out spread between the two symbols —
the exact small-sample/single-symbol-driven pattern Lesson #10 already
warns against, so not treated as a better answer. **Caveat**: this whole
grid tops out at 9 trades total (2 symbols vs. the stock backtest's 7),
nowhere near as robust as the stock-side validation — "not contradicted,"
not "independently re-derived as optimal." Revisit once more crypto
trades accumulate.

**Re-entry threshold (5% / 12.5%)**: crypto's value must stay strictly
below `CRYPTO_TRAILING_STOP_THRESHOLD` (20%), not equal to it —
`check_positions()` runs stop-loss → trailing-stop → re-entry in that
order and skips re-entry once a position is closed. Setting them equal (an
actual bug shipped and caught same-day, 2026-07-14) meant the trailing
stop always closed the position at the same pullback level before
re-entry could ever fire, making it dead code. 12.5% preserves the same
ratio as the stock config (5%/8% = 0.625, applied to crypto's 20%) rather
than a new guess.

**Re-entry pullback is measured from the tracked peak, not entry price —
easy to conflate, worth being explicit about.** `_handle_reentry` computes
`(peak_price - current_price) / peak_price`, using `position_peak_prices`
(the highest price observed since the position opened or its last reset),
**not** `(entry_price - current_price) / entry_price` (a position's
unrealized P&L). These can diverge substantially — e.g. the test account's
AMZN sat around -3% unrealized P&L while its pullback from its own tracked
peak was already past 6%, well over `REENTRY_THRESHOLD`. Don't infer
whether a position is near a re-entry from its P&L display; check the
peak-relative figure specifically. Also note: `reentry_fired` clears only
on a *new* peak, so a position can sit past `REENTRY_THRESHOLD` from its
peak indefinitely without re-entering, if it already fired once for that
peak and hasn't since made a new high — see `KNOWN_LIMITATIONS.md` "State
that matters" for the live AMZN example of exactly this.

**Re-entry minimum age (4h, both stock and crypto)**: added after a real
reentry fired on GOOGL (test account) ~2.5h after its original scan-buy,
same trading session — `position_peak_prices[symbol]` is set to
`current_price` on the very first check after entry, so an ordinary
intraday dip right after a fresh fill looked identical to a real pullback
from an established peak. Backtested the gate itself against 300 real
daily bars across both watchlists (full walk-forward simulation: entry,
stop-loss, trailing-stop, reentry blended into average cost, scanner exit
— not just raw signals): every historical reentry in that window fired 7+
trading days after its entry (soonest: NVDA at 7 days), so a gate anywhere
from a few hours to several days costs zero backtested expectancy — picked
4h as comfortably clear of the observed 2.5h failure while staying well
below the shortest real legitimate gap seen. Not asset-class-split like
the thresholds above — this isn't about volatility magnitude, it's about
whether the tracked peak is old enough to be meaningful, which applies the
same way to crypto and stocks.

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
- **Wider watchlist, attempt 2: smooth-trending blue chips** (META, AVGO,
  COST, JPM, V, MA, WMT), tested 2026-07-23 specifically to rule out
  "high-beta was the problem" from attempt 1 above. Same failure mode
  anyway: added individually to the current 7, **every single one**
  reduced the combined expectancy (best case JPM: +1.461%/trade vs.
  baseline's +1.496%; worst case AVGO: +0.810%/trade, down from just 2
  trades at -8.448%/trade standalone). All 14 combined: +0.470%/trade
  (43.6% win) vs. the current watchlist's +1.496%/trade (48.1% win) alone.
  Not outlier-driven this time either (leave-one-out on the combined set
  never went negative) — it's a broad-based dilution, not one bad symbol.
  **Two independent widening attempts, two different symbol-selection
  theories, both failed** — this specific 7-symbol combination has an
  edge that doesn't obviously transfer by adding more names, regardless
  of volatility character. Don't re-propose "just add more symbols"
  again without a fundamentally different rationale (e.g. a large
  systematic screen across dozens of candidates, not another hand-picked
  handful) — see the open item below.

- **Wider watchlist, attempt 3: systematic 59-candidate screen**, tested
  2026-07-23. Screened a broad, diverse set of liquid large/mid-caps
  across sectors (not hand-picked for a theory this time), kept only
  candidates with a positive standalone edge (≥3 trades) AND a positive
  marginal contribution when added to the current 7 — 14 survived both
  filters (CAT, ADI, LRCX, IWM, AXP, INTC, LLY, BAC, C, MRK, GS, LMT, HD,
  CSCO). In-sample this looked dramatically better: 86 trades,
  +3.209%/trade, 55.8% win vs. baseline's +1.496%/trade, 48.1% win — and
  passed leave-one-out cleanly (never went negative excluding any one
  symbol). **Looked great and was wrong.** Re-tested against a genuine
  holdout the screen never saw (an older, non-overlapping 300-bar window,
  roughly 2024-02 to 2025-04, vs. the ~2025-04-to-2026-07 window the
  screen actually ran on): baseline alone still positive (+1.258%/trade),
  but baseline + the 14 "survivors" **underperformed baseline alone**
  (+0.909%/trade) on that unseen period. The in-sample edge was
  curve-fitting from searching 59 candidates on one window, not a real
  pattern — leave-one-out only catches one outlier symbol propping up an
  aggregate, it does NOT catch an entire selection process overfitting to
  the window it was run on. **Not implemented.**
  **Methodological lesson for any future systematic screen**: reserve a
  genuine out-of-sample holdout *before* selecting candidates, never
  select and validate on the same window — this applies beyond watchlist
  screens to any future backtest that searches across many candidates
  (parameters, symbols, timeframes) rather than testing one specific
  hypothesis.
  **Three independent widening attempts (high-beta names, blue-chip
  names, systematic screen) have now all failed** to beat the current
  7-symbol watchlist. This is a reasonably strong signal that this
  specific 7-symbol combination is close to a local optimum for this
  exact strategy, not that watchlist search is inherently hopeless — a
  future attempt would need either a properly out-of-sample-validated
  screen from the start, or a fundamentally different approach (e.g.
  correlation/sector diversification analysis instead of pure backtest
  performance).

Tested 2026-08-07 against 600 real daily bars (proper 300/300 train/holdout
split, full walk-forward simulation including stop-loss/trailing-stop, not
just raw scanner signals), specifically looking for a higher-expectancy
variant before considering real money:

- **Trend/regime filter** (only buy when price is above its own 50-day or
  100-day SMA). Worse on both train and holdout (+0.27% to +0.39%/trade vs.
  baseline's +1.955%/trade holdout), and outlier-driven — sign flips
  excluding AMZN (50-day) or AAPL+AMZN (100-day). A trend filter cuts into
  exactly the pullback-then-recovery entries this strategy already profits
  from; it doesn't add a real edge here.
- **Volume confirmation on entry** (require entry-day volume > 1.2x or 1.5x
  the 20-day average). Looked spectacular on holdout (+3.3% to +9.2%/trade,
  one variant 100% win rate) but on only 3-8 total trades across the whole
  window — the exact thin-sample/outlier pattern flagged repeatedly
  elsewhere on this page (1.2x flips negative excluding AAPL alone). Reject
  as unvalidatable at this trade frequency, not "worse," just untestable.
- **Partial profit-taking** (sell 50% of the position at +8% or +15% gain,
  let the rest ride under the normal stop/trail rules). Dollar-weighted
  return was worse than baseline at both triggers (+1.80% and +1.88%/trade
  vs. baseline's +1.955%). This directly contradicts the "let winners run"
  thesis `SELL_RSI_MIN=80` was tuned for on 2026-07-16 — taking profit early
  is exactly what would have clipped the real MSFT trade (+23.9%) short.
- **ATR-based (volatility-adaptive) stop-loss/trailing-stop**, replacing the
  fixed 5%/8% with a multiple of each symbol's own 14-day ATR. **Initially
  looked like a real win** (+2.36%/trade on holdout at a 2x/3x multiplier)
  — but that number came from eyeballing several multipliers against
  holdout data, the same shopping-around the systematic-screen mistake
  below already warns about. Re-run properly (grid-search the multiplier on
  **training data only**, then check that one selected value against
  holdout, never touched during selection): the train-optimal multiplier
  (1.5x stop / 2.25x trail) scores **+1.76%/trade on holdout — slightly
  worse than baseline's +1.955%**, not better. The earlier "win" doesn't
  survive proper selection discipline. **Not implemented.**
  **Methodological note**: this is the same failure mode as the systematic
  watchlist screen below, just at a smaller scale (one parameter instead of
  59 candidates) — testing multiple variants and reporting whichever one
  looks best on holdout is itself a form of overfitting, even when each
  individual variant's train/holdout split is done correctly. Any future
  parameter sweep must select on train only and touch holdout exactly once,
  for the one selected candidate.
- **Conclusion**: none of the four ideas above beat the current baseline
  (65/80 RSI, fixed 5%/8% stops) once properly tested. The current strategy
  already captures most of the readily-available edge in this simple
  EMA9/21+RSI framework — see "Open items" below for untried, larger-scope
  directions (position sizing, different indicators) if this is revisited.

**Volatility-adjusted position sizing**, tested 2026-08-11 against the same
600-bar/300-300 train-holdout window, comparing dollar-weighted total return
(the right metric here — sizing doesn't change which trades happen, only how
much capital each gets, so %/trade is the wrong yardstick). Flat $10/position
baseline: **+1.682%** holdout dollar-weighted return. Sizing positions
inversely to each symbol's own 14-day ATR (targeting equal $ risk instead of
equal $ notional per trade) scored **worse at every target-volatility tested**
(1.5%/2%/2.5%/3%, clipped 0.5x-2x of base): +1.249%, +1.309%, +1.320%,
+1.433% respectively. Not outlier-driven — leave-one-out stayed positive for
every excluded symbol at every setting. **Root cause, not just a number**:
on this specific watchlist, NVDA is both the highest-volatility name *and*
the best performer (+2.60% in baseline leave-one-out, the top of the list).
Volatility-adjusted sizing systematically shrinks NVDA's position (high vol
→ smaller size) while growing SPY/QQQ's (low vol, index-like → bigger size)
— but SPY/QQQ are the weaker performers here. Risk-parity-style sizing
assumes volatility and edge are independent; on this watchlist they're
positively correlated, so de-weighting by volatility fights the actual edge
instead of protecting it. **Not implemented.** Would need re-testing if the
watchlist ever changes (a future screen might land on symbols where this
correlation doesn't hold), not a permanently-closed question the way the
07-16/07-23 watchlist-widening attempts are.

**Dual-signal entry: Bollinger + EMA, stocks only (live 2026-09-03).**
Main went 2 full trading days (19 symbols, zero BUY signals) because
EMA9/21+RSI<70 (a local, unbacktested loosening of `BUY_RSI_MAX`) can't
structurally fire while the market stays broadly overbought — further
loosening the same gate was tried before and gutted expectancy, so wasn't
the fix. A sibling branch (`origin/master`, built for a now-retired bot,
never adopted here) already had a validated answer for exactly this: an
independent Bollinger mean-reversion signal that fires under the opposite
condition (oversold, not overbought). Real backtest, 22 months/460 daily
bars, walk-forward with realistic sizing/stops/reentry: Bollinger alone —
44 trades, 70.5% win, +1.66%/trade, +16.67% total, positive train and
holdout, leave-one-symbol-out always positive. **Dual (both sources
together) — 90 trades (~2x the frequency), +1.71%/trade (higher than
either alone), +35.89% total**, positive on train (+18.25%) and holdout
(+15.70%). A prior attempt to fix low frequency by loosening Bollinger's
own thresholds was tried and rejected — every looser variant traded more
with worse/negative expectancy; dual-sourcing, not loosening, is what
validated. Ported as an additive second signal, not a replacement.
`BUY_RSI_MAX` was reverted 70→65 alongside this — the dual-signal backtest
above was run at 65, so shipping the EMA leg as something actually tested
rather than stacking an unvalidated threshold on top of a validated
strategy change. **Real caveat: never forward-tested live before this** —
backtest-only until the flag went on. First live result: IWM bought
2026-09-03 13:57 via the Bollinger path (RSI 34.9, lower-band bounce),
veto=False, tracked in `position_method_state.json` as `bollinger`. One
trade proves nothing about the 70.5%/+1.66% edge by itself — revisit
alongside milestone 1 below once more accumulate. Sofi runs the identical
code/flag; Nova doesn't share this codebase (different multi-timeframe
strategy) and wasn't touched.

## Automated monitoring: `strategy_check.py`

A VPS cron job (hourly, test account only, `/opt/alpaca-bot-test`), added
2026-07-16 in direct service of this doc's stated goal. Alert-only —
never trades, never edits thresholds — via the same Telegram/cooldown
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

**Live/backtest EMA methodology gap found, then fixed same day (2026-07-16,
committed after this backtest).** Every backtest on this page (including
the SELL_RSI_MIN change above) computes one continuous EMA9/EMA21 series
over the whole fetched window. But `OpportunityScanner._analyze` — the
actual live code — called `get_bars(symbol, limit=35)` fresh on every
single check and computed EMA9/21 from scratch on just those 35 bars each
time, with no continuity between checks (each call's EMA "seed" is a plain
average of whichever 9/21 bars happen to be first in that day's window).
Tested both against identical live data: continuous EMA reproduced the
documented 65/80 result almost exactly (26 trades, +1.352%/trade,
leave-one-out +0.71% to +1.89% — matches "not outlier-driven"); the
literal 35-bar-window-per-check version gave a materially weaker,
outlier-driven result (19 trades, +0.914%/trade, flips negative excluding
AAPL).

**Root cause, not just symptom:** `_compute_ema`'s seed (a plain average
of the first `period` bars in whatever window it's given) needs room to
smooth forward before it's trustworthy — EMA21 (k≈0.091) needs ~40-60
steps, and 35 bars only gave it ~14. Backtested the windowed methodology
itself at increasing window sizes (35/45/60/75/90/120/150) against the
same real data: results converge to match the continuous-EMA baseline
exactly from **75 bars for stocks, 90 for crypto**, and stay identical
beyond that (confirmed no further change out to 150). Below 75, weaker and
outlier-driven; at 45, still measurably short of full convergence. Chose
**90** (`scanner.py: OpportunityScanner.SIGNAL_BAR_WINDOW`) as a single
value safe for both watchlists. Verified no immediate side effect before
deploying: the account's one open position (NVDA) flips from a fresh BUY
signal to HOLD under the wider window, but since it's already held,
neither signal changes any actual order (no duplicate-buy path exists,
HOLD does nothing) — confirmed via `get_positions()` this was the only
open position. `trader.py`'s separate re-entry RSI gate (`limit=35` at
line ~329) was deliberately left unchanged — it only calls `_compute_rsi`,
which is window-length-invariant beyond ~15 bars, so it was never affected
by this gap.

## Path to real money

Not a backtest-driven decision — the strategy is already as validated as
this framework's backtests can make it (see "Rejected hypotheses" above:
five separate attempts to beat it all failed). The remaining gate is real
forward-test evidence on the live test account, tracked as three concrete
milestones:

1. **10 closed stock trades**, with `strategy_check.py`'s own forward-test
   comparison (`trade_history.json` vs. a fresh backtest) showing live
   results tracking the backtest, not diverging. As of 2026-09-04: **3 in
   `trade_history.json`** (MSFT +24.5% scanner RSI exit 08-04, GOOGL -5.2%
   stop-loss 08-11, AMZN -5.1% stop-loss 08-14) **+ 1 earlier (NVDA -5.19%
   stop-loss 07-17) confirmed via VPS logs but predating the forward-test
   file itself** (`_save_trade_history` was only added 2026-08-04) — not
   missing/lost data, just before the tracking code existed. **Stalled,
   not just slow**: zero trades have closed since 08-14, three weeks as of
   this update — Main's been sitting on open positions (currently AAPL,
   IWM) waiting for a stop/trail/exit to fire, not generating new closes.
   This is the actual bottleneck to real money now, not backtest
   confidence.
2. **1 closed crypto trade — met.** BTCUSD -2.19% stop-loss, 2026-08-13,
   confirmed in `trade_history.json`.
3. **The `MIN_REENTRY_AGE_HOURS` gate observed firing correctly on a real
   pullback dated after 2026-08-06** (either a real re-entry buy, or a
   `REENTRY_SKIPPED` for a too-young position). Not yet observed — see the
   `reentry_fired`-latch note above for why the test account's AMZN
   specifically can't be the one to demonstrate this without a new high
   first.

Once all three are met, that's the point for an actual go/no-go
conversation on switching to real money — not a fixed date, not a better
backtest number. Revisit sooner only if real trade outcomes start
diverging materially from what the backtest predicts (the exact thing
milestone 1's forward-test comparison is built to catch).

## Open items — real, deliberately not acted on

- **Whether the pre-fix 35-bar EMA windowing affected any *already-placed*
  real trade** (vs. only being visible in backtest) is unverified and
  unknowable after the fact — not revisited, since the fix is forward-only
  and the account has too little trade history yet to check retroactively.
- ~~`adjust_strategy()` mutates `STOP_LOSS_THRESHOLD`/`REENTRY_THRESHOLD` at
  runtime with no backtest behind it~~ **Resolved 2026-07-16**: backtested
  (full-system simulation — real entries/exits/stop-loss/trailing-stop, 300
  daily bars, ~14 months, test-account sizing) and found it underperformed
  fixed thresholds (+6.66% vs +7.65% total return) while firing often (17
  threshold changes across 25 closed trades, repeatedly ratcheting
  stop-loss across its full 2%-10% range). Live call removed from
  `scheduler.py`; `trader.py: adjust_strategy()` left intact, not deleted.
  Caveat: this backtest is a single historical path (one specific streak
  sequence), not an aggregated/leave-one-out-validated result like the RSI
  thresholds — directional evidence, re-visitable with a more robust
  multi-window backtest if desired.
- **Wilder-smoothed vs. Cutler's-style RSI** — `_compute_rsi` is the
  latter; every threshold above was tuned against it, so it's internally
  consistent, but untested whether Wilder smoothing would perform
  differently. Needs a backtest before any change.
- **`win_streak`/`loss_streak` persistence** — still not fixed alongside
  `peak_prices`/`reentry_fired`. Lower stakes now that `adjust_strategy()`
  is disabled (streaks are display-only in the daily report, per
  `analyze_performance`), but left as-is rather than revisited, since
  fixing it would only matter again if `adjust_strategy()` is re-enabled.
- ~~Crypto's 60-min check interval has no session-close bound~~
  **Resolved 2026-07-16**: `CRYPTO_CHECK_INTERVAL_MINUTES` default lowered
  60 → 15 (both services, no `.env` override on either) — kept looser than
  stock's 5-min interval since crypto's stop-loss/trailing-stop (15%/20%)
  are already wider to tolerate crypto's higher ordinary volatility.
- **Shared buying-power pool between the stock and crypto scheduled
  jobs** (separate timers, each fetches its own fresh `get_account()`) —
  narrow remaining race if both fire in the same window; fails safe via
  Alpaca's own order validation (rejected order, not overdraft).
- **Untried, larger-scope profitability directions** (2026-08-07, updated
  2026-08-11): the four hypotheses tested 08-07 (trend filter, volume
  filter, partial profit-taking, ATR stops) plus volatility-adjusted
  position sizing (tested 08-11, see above — rejected, watchlist-specific
  vol/edge correlation) were all same-scale tweaks and none beat baseline.
  Still genuinely untried: Kelly-criterion sizing specifically (distinct
  from volatility-adjusted — sizes off estimated win-rate/payoff ratio
  rather than volatility, so wouldn't share the same failure mode, but
  needs a stable win-rate estimate this account's ~31-trade backtest
  sample may be too thin to trust) and different indicators entirely
  (MACD) rather than filters layered on the existing EMA9/21+RSI core —
  real time investment, not a quick follow-up, not started. Bollinger
  Bands (the other indicator this item used to list) is no longer
  untried — shipped 2026-09-03 as a dual-signal addition alongside EMA,
  not a replacement; see the dual-signal note above.
- **Drought fallback** (a Bollinger variant that widens/relaxes further
  during an extended no-signal drought) exists on the same sibling branch
  the dual-signal port came from, but wasn't ported alongside it — its own
  backtest note admits only a single occurrence (n=1) of validation, and
  stacking a second, weakly-validated behavior on top of the
  already-first-ever-live-test of dual-signal Bollinger would make any
  early live anomaly impossible to attribute cleanly. Cheap to add later
  as its own separately-flagged follow-up once dual-signal has real live
  results.

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
