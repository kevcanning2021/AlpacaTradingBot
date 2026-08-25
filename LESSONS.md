# Lessons Learned

Durable principles from real mistakes/incidents on this project. Kept here (not
just in dev tooling memory) so anyone reading the repo — including future me —
gets them, not just whoever's driving a particular session.

1. **A safety-check wrapper must raise on failure, never return an empty/falsy
   value.** `get_positions()` returning `[]` on a failed request once made the
   "already holding this symbol" guard fail open and caused a duplicate buy.

2. **Streak/adjustment logic must be driven by realized (closed-trade) P&L,
   never a snapshot of concurrent open positions.** Correlated assets dipping
   together once looked like a false loss streak.

3. **Anything gated to "market open/close" must derive from the market's own
   timezone (ET), not the server's or the report's display timezone.**

4. **One global threshold rarely fits a heterogeneous watchlist.** Backtest
   any new threshold across the *whole* watchlist, not just the symbol that
   prompted the change.

5. **Before calling any method that wraps `create_order`/`close_position` with
   test/synthetic input, stub the client or reason through the code statically
   — don't call it against the real API "just to check the branching logic."**
   Doing this once closed a real position by accident (no harm that time, not
   guaranteed next time).

6. **`git pull` into a production deploy is not safe without diffing first.**
   Two services on the same branch name are not necessarily on the same
   commit. Always `git log HEAD..origin/master --oneline` (or check the file
   list) before pulling into anything live.

7. **A report's suggested fix can assume third-party API capability that
   doesn't exist — verify against the actual endpoint/docs before
   implementing,** and ship+document a partial fix rather than assuming full
   coverage because the general idea sounded implementable.

8. **An aggregate backtest number can be one outlier symbol in disguise.**
   Re-run with each symbol excluded in turn before trusting an aggregate,
   especially on a small watchlist or a sharp result at higher
   thresholds/parameters.

9. **Local dev/backtest scripts sharing live API credentials with a deployed
   service can degrade that service in real time.** Heavy local `get_bars()`
   batches once caused ~15 minutes of read timeouts on the live bot. Avoid
   large local API batches overlapping market hours if credentials are shared.

10. **A new order-placing code path needs the same entry-quality gates as
    existing ones, not just the same operational safety mechanisms
    (cooldowns, buying-power checks, etc).** Explicitly diff a new buy path's
    entry conditions against the existing one's — don't just verify it's safe.

11. **Verifying something out-of-band doesn't help a stateless automated
    reviewer unless the finding is pushed into its own prompt/config.** A
    routine with no memory across runs and no access to your session will
    keep re-flagging the same resolved question otherwise.

12. **Don't generalize "true of every case I've checked" to "true of this
    category" without checking which dimensions were actually varied.**
    Wrong twice in a row on the same bug (`watchdog.py` order `source` field)
    because timing and side had never actually been tested together, not
    because the reasoning was checked and held.

13. **Any multi-candidate/multi-parameter backtest search needs an
    out-of-sample holdout reserved *before* the search runs.** A 59-candidate
    screen looked dramatically better in-sample and passed leave-one-out, but
    collapsed on a genuine holdout window — the search process itself had
    overfit to the window, which leave-one-out alone doesn't catch.

14. **An "API says success but the client doesn't show it" mismatch can be
    server-side scope/precedence shadowing, not a client bug.** A Telegram
    bot command menu that wouldn't show despite confirmed server-side
    registration turned out to be a stale higher-precedence scope from
    earlier manual testing.

15. **A monitoring/watchdog script needs its own error handling around the
    exact same API calls it's meant to be watching.** An unhandled exception
    fetching positions/orders used to crash the watchdog before it could
    report anything at all — one bad API call took out its own alerting.

16. **Any peak/trough-tracking state needs a minimum-age guard, not just an
    economic threshold, if the state resets on a fresh episode.** A
    pullback-triggered re-buy fired ~2.5h after its own original entry
    because the tracked "peak" was seeded to the entry price on the very
    first check — an ordinary intraday dip right after a fill looked
    identical to a real pullback from an established high. The fix isn't a
    tighter/wider threshold, it's asking "how long has this state actually
    existed" before trusting it.

17. **Testing several candidate variants and reporting whichever one looks
    best on holdout is itself overfitting — even if each variant's own
    train/holdout split was done correctly.** Comparing 8 ATR-stop
    multipliers and picking the one with the best holdout number produced a
    false "win"; re-selecting the multiplier from training data only, then
    checking that one choice against holdout exactly once, gave the honest
    (worse) answer. Lesson 13's out-of-sample discipline applies to informal
    side-by-side comparisons too, not just formal systematic searches.

18. **A long-lived service's entry point must guard all side-effecting code
    behind `if __name__ == '__main__':`, not just wrap it in a function.**
    `run_server.py` called `scheduler.start()` at module level — a plain
    `import run_server` (e.g. from a verification/test script, not even
    running it) silently started a real scheduler against whatever `.env`
    was active, with no way to tell from the import alone that it had
    happened. Caught and killed before any interval elapsed, but the module
    boundary between "safe to import" and "starts doing things" needs to be
    the `__main__` guard, every time, for anything that runs as a service.

19. **A threshold computed relative to one reference price is not the same
    number as a same-looking % against a different reference — don't infer
    one from the other.** `REENTRY_THRESHOLD` is pullback-from-*peak*, but a
    position's displayed unrealized P&L is relative to *entry* — these
    diverged by several percentage points on a real position (P&L -3%,
    pullback-from-peak +6.5%), producing a factually wrong "hasn't hit the
    threshold" statement from reasoning off the wrong number. When a
    threshold has a specific reference price, state which one explicitly
    rather than assuming the closest-looking displayed percentage is it.

20. **A "fires once per episode" flag can stay latched from before a new
    gate condition existed, silently blocking that gate from ever being
    observed — and this looks identical to "the gate just hasn't been
    tested yet."** A minimum-age gate was added to `_handle_reentry`, but
    the one position whose pullback looked closest to re-testing it had
    already fired its one re-entry *before* the gate existed — its flag
    never got a chance to interact with the new age check at all, and
    won't until a new peak resets it. Current price/pullback data alone
    can't distinguish "about to test the new logic" from "structurally
    can't reach it yet" — check the actual gating state, not just the
    metric the gate nominally responds to.

21. **A short live window looking flat or negative doesn't mean a strategy
    is broken — check the historical distribution of same-length windows
    for that exact strategy before concluding anything.** After ~1 month
    live the test account sat near breakeven, and it felt like proof
    something needed fixing. Computing every historical ~1-month rolling
    window from the same 22-month backtest showed 33% of all such windows
    were negative and the median was only +0.20% — the live result was
    almost exactly the median outcome, not an outlier. Lesson 8's
    leave-one-out check catches a *symbol* outlier hiding inside an
    aggregate; it says nothing about whether a given *time window* is
    unusual. These are different axes of variance and need different
    checks — compute the actual rolling-window distribution before
    treating a flat or bad stretch as a signal that something's wrong.

22. **When a live strategy's core signal logic changes, grep the whole repo
    for every standalone reimplementation of the old logic — don't assume
    the primary module is the only copy.** Swapping the stock scanner from
    an EMA9/21 crossover to Bollinger Band mean-reversion only required
    editing `scanner.py` itself, but `strategy_check.py`'s daily
    drift-detection backtest and `telegram_bot.py`'s `/optimize` grid
    search each had their *own* independent reimplementation of the old
    EMA logic for their own on-demand backtesting. Left alone, both would
    have silently kept validating and grid-searching a strategy that was
    no longer actually deployed, producing plausible-looking numbers for
    the wrong strategy. Any tool that reimplements core logic instead of
    calling the live version (usually for performance or backtest-speed
    reasons) needs to be found and moved in lockstep with it.

23. **Even a change that feels "just a doc update" needs the same
    diff-check discipline as any other pull (Lesson 6) — don't skip it
    because the *intent* seems low-risk.** Pulled a docs-only commit into
    both VPS clones without first checking `git log HEAD..origin/master`
    on each one individually; production's clone turned out to be two
    commits behind, not one, so the same pull silently also brought in a
    same-day strategy-logic swap that had deliberately not been approved
    for production yet. Caught before any restart could load it, reverted
    with `git reset --hard` back to the pre-swap commit. What a change
    *feels* like it should touch says nothing about what a given clone's
    pull will actually bring in — the diff-check has to run every time,
    on every target, regardless of how small or safe the intended change
    seems.

24. **Searching a multi-day bar window for "the bar at hour X, minute Y"
    without also filtering by date will silently return the wrong day's
    bar, not an error — and the failure is invisible unless checked against
    the raw data directly.** Happened in the sibling `pdt15rev-bot` project,
    not here, but the bug pattern applies anywhere this codebase fetches a
    multi-day bar window and looks for a specific time-of-day bar: querying
    a small number of recent 15-min bars right at a session's start pulled
    in several prior trading days to fill the window, and an unscoped
    `hour == X and minute == Y` search returned the *first* match in the
    ascending-sorted list — the oldest matching day, not today. A whole
    day's worth of trading decisions ran against the wrong reference
    range, and every log line looked completely normal; nothing about the
    output signaled a problem. Found only by re-fetching the specific bar
    directly from the API and comparing it byte-for-byte against what got
    captured live. Any time-of-day bar lookup against a window that could
    span more than one day needs an explicit same-day filter, not just an
    hour/minute match — and don't trust a quiet, error-free log as proof
    the right data was used.

25. **A backtest that looks positive in aggregate or across rolling
    windows can still be actively unprofitable *right now* — check recent
    performance specifically before deploying a new candidate, not just
    the full-history or rolling-window summary.** Comparing Donchian
    breakout against Bollinger mean-reversion for a new crypto strategy,
    Bollinger's full-window and rolling-window numbers looked reasonable
    (69-87% of 90-day windows non-negative), but its most recent 9 months
    were badly negative on both symbols (-34.56%/-50.88% total) — it had
    been repeatedly buying dips into a real, still-ongoing decline.
    Lesson 21 already established not to panic over a single bad window
    given historical variance for an *already-deployed* strategy; this is
    the inverse case — for a *candidate* being considered for deployment,
    the most recent window is the one that actually matters most, since
    that's the regime it would start trading in immediately. A strategy
    can pass every historical robustness check and still be the wrong
    choice today if its current trajectory is bad.
