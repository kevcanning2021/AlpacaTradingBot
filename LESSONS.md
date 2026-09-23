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

21. **A committed, pushed, and even live-verified fix isn't actually live
    until the long-running service that imports it is restarted.** Main and
    Nova's trading processes (`run_server.py`/`run_paper_bot.py`) import
    `research_agent.py` once at startup and hold that module in memory for
    the process's lifetime. Several real research-agent fixes made across a
    week sat correctly on disk, and each one was even confirmed correct by
    running it in a fresh one-off script — but the actual live bots kept
    using the old, buggy version underneath, because each had been running
    since before the fix and Python never re-imports an already-loaded
    module. Nova kept re-vetoing the same false-positive article for hours
    with the "fixed" code sitting unused the whole time. Cron-invoked
    scripts (`watchdog.py`, `daily_fleet_audit.py`) don't have this problem
    — cron spawns a fresh process every run, so they always pick up current
    code automatically. After deploying to anything a persistent service
    imports, `systemctl restart` and confirming the new uptime is a
    mandatory last step, not an optional one — "verified in isolation" is
    not the same claim as "the running process is using it."

22. **A derived/summary metric answers exactly the question it was built
    for, not the nearby question it sounds like it answers — check live
    ground truth before restating it as a broader claim.** `daily_fleet_
    audit.py`'s `forward_test.closed_trades` counts only round-trip
    (opened-and-closed) trades. Sofi's audit correctly showed `closed_
    trades: 0` for four straight days after the watchlist widening, which
    got reported as "hasn't traded" — but it had actually opened two real
    positions (COST, UBER) on day one; they just hadn't closed yet. This is
    the exact same distinction ("zero closed trades" vs. "zero orders of
    any kind") already learned once during the original drought
    investigation that led to the widening in the first place — relearned
    the hard way because a multi-day pattern matching a prior expectation
    ("this looks like the drought again") got restated from a summary
    number instead of re-verified against actual open positions.

23. **Before concluding "not enough data," check whether the rule is
    structurally self-starving — a conjunction of near-instantaneous
    events is multiplicatively rare, and that looks identical to a data
    shortage.** An intraday strategy produced only 13-20 trades over ~6
    months across 15 symbols, twice. Both times the conclusion drawn was
    "the IEX free feed's ~6-month 5-minute history isn't enough raw
    material" — and STRATEGY.md briefly carried a confident
    recommendation to buy a paid data plan. That was wrong. The real
    cause was mechanical: the entry required a 15m Bollinger bounce (a
    two-bar event) and a 5m EMA cross (a one-bar event) to be true on the
    *same* 5-minute bar. Two rare instantaneous events demanded to
    coincide exactly is their probabilities multiplied. Restructuring so
    the 15m setup *arms* a one-hour window for the 5m trigger — which is
    what a discretionary trader actually does — produced **281-296 trades
    from the exact same dataset**, ~15x more, and finally made the
    strategy decidable (it had no edge, which is a real answer). The
    diagnostic question to ask first: *how many bars is each condition
    true for, and am I requiring them to overlap?* Sample starvation is a
    rule-design bug far more often than a data-availability one, and the
    fix is free while the misdiagnosis costs money.

24. **Pre-register what counts as a conclusive result before running the
    test, not after seeing the numbers.** The same backtest harness
    initially passed anything with `holdout expectancy > 0 and n >= 5`.
    Two separate attempts cleared that bar on n=7 and n=10 and were
    reported as "positive" when both were statistically meaningless — at
    that size a positive expectancy is indistinguishable from a coin
    flip, so the threshold let a non-answer masquerade as a pass. Raising
    it to n >= 50 *before* the third run, and stating the criterion out
    loud first, meant the result could only come back as one of three
    honest outcomes (inconclusive / pass / clear negative) rather than
    being rationalized after the fact. Decide the bar while you still
    don't know which side of it you'll land on.

25. **If you are optimising for X, don't select your parameter on Y — the
    selection will quietly pick the variant that least serves your actual
    goal.** A Donchian breakout was added as a third entry signal on Main
    for one reason: **more trades**. Its period N was then selected the
    way every other parameter in this project gets selected — best
    expectancy on train. Across N=10/20/40/55 the relationship was cleanly
    monotonic: shorter N added far more trades and scored worse on
    expectancy (N=10: +224 trades, +0.616%/trade train; N=55: +86 trades,
    +1.928%). So "best train expectancy" mechanically chose **N=55, the
    variant that added the fewest trades** — optimising against the very
    thing the change existed to deliver. The pre-registered "+50% trade
    count" criterion then failed at 1.49x, missing by a hair, *because of
    the selection rule*. The habit of always selecting on expectancy is
    usually right and was wrong here: when the objective is frequency
    subject to an expectancy floor, select the **most trades among
    variants clearing the floor**, not the best expectancy outright.
    (Caveat worth keeping: fixing the selection would not have rescued
    this particular test — holdout expectancy failed independently, with
    Donchian's own holdout trades running **negative** at -0.434%/trade
    across n=74. The methodological point stands on its own regardless.)

25. **A keyword match tells you a word is present, not what it was
   said about — a verb attaches to whatever noun precedes it, which is
   frequently not your asset.** 'plunge' blocked ETH/USD for 33
   consecutive checks across 2.5 days on the headline "Ethereum Reclaims
   $2,600 After a Week — Is a Bigger Rally Ahead?", whose summary reads
   "Ethereum reclaims $2,600 as fees plunge". Transaction *fees* were
   falling, which is good for Ethereum, inside an explicitly bullish
   article. This was the fifth distinct false-positive class in this
   keyword checker, and the first where no article-level filter was even
   arguably at fault: one symbol tagged, no roundup phrasing, not an index
   ETF, and the asset genuinely named in the text. Every earlier fix asked
   "is this article really about my symbol?" — the right question here was
   "is this *word* really about my symbol?", which substring matching
   cannot answer at all. The durable split: **event keywords** ('lawsuit',
   'bankruptcy', 'restated', 'recall') name something that happened TO a
   company and barely admit another subject; **price-action verbs**
   ('plunge', 'plummet', 'slump') take any subject at all — fees,
   volatility, yields, volume, short interest — so they are structurally
   unreliable regardless of how the surrounding filters are tuned.
   Removed the whole family rather than adding a sixth patch. 'crash' was
   kept deliberately, being a real corporate event when it is a vehicle
   rather than a market. When a rule keeps failing in new ways, check
   whether the failures share a *category* before writing another
   special case for the latest one.

26. **A transient alert is not a monitoring system — if an alert
   clears itself the moment the condition pauses, anyone who checks
   periodically will almost always see nothing.** `watchdog.py`'s
   `check_new_log_errors` inspects each unit's journal only *since the last
   watchdog run* (a 15-minute window), and `main()` deletes any alert key
   not re-raised on the following run. Nova logged 51 failed crypto orders
   across five separate days and every periodic check of `active_alerts`
   correctly reported zero: with the errors spread over ~768 watchdog runs
   the alert was live roughly 3-4% of the time, so a snapshot read had a
   ~96% chance of seeing an empty dict. The Telegram pings all fired, so a
   human watching a phone had strictly better information than anything
   reading the state file. **Snapshot state answers "is something broken
   right now", which is a much weaker question than "has anything been
   going wrong"** — and intermittent recurring failure is the most common
   shape a real problem takes. Any current-state check needs a
   time-windowed history check beside it (`journalctl --since '24 hours
   ago' | grep -cE 'ERROR|Traceback'`), and any alert meant to be noticed
   by something that polls needs to either persist until acknowledged or
   carry a recurrence count, rather than silently vanishing.

27. **Fixing a blocker exposes every latent bug in the code path it
   was blocking -- expect the next failure immediately, and look for it.**
   Nova's crypto orders had failed on sizing for weeks, so not one had ever
   filled. Within three hours of that being fixed, a second bug surfaced
   that had been dormant the entire time: Alpaca orders crypto as
   'ETH/USD' but reports the position as 'ETHUSD', so every
   'symbol in open_position_symbols()' check silently evaluated False.
   That let ETH be bought twice against a 25% per-position cap, made
   reconciliation record each position's own BUY as its exit (fabricating
   two wins), and orphaned a live position with no journal row -- meaning
   no stop-loss at all, since crypto has no bracket/OCO at Alpaca and the
   exit monitor only reads journal.open_trades(). None of it was reachable
   while sizing was broken. The rule: after unblocking a path that has
   never actually executed, treat everything downstream as untested
   regardless of how long the code has existed, and go looking rather than
   waiting to be told. A fix that makes a feature work for the first time
   is a deployment of that whole path, not a one-line change.

28. **Rounding a quantity is directional: round() on a SELL asks for
   more than you hold and is rejected outright.** broker.submit_market_order
   used round(quantity, 6), which rounds half UP. A real 0.010044825 ETH
   position became a request for 0.010045 -- 'insufficient balance for ETH
   (requested: 0.010045, available: 0.010044825)' -- and the exit then
   failed on every poll, 137 times across two hours, while the stop sat
   triggered and the position had no way out. It survived only because the
   price recovered above the stop on its own, which is luck, not
   protection. Truncate rather than round for any order quantity (_floor6):
   under-requesting is recoverable on both sides, over-requesting is not.
   Better still for a full exit, do not compute a quantity at all --
   Alpaca's close_position liquidates exactly what is held, which also
   avoids leaving dust, and dust is not cosmetic here: any remainder keeps
   the symbol in get_open_position_symbols() and would block re-entry on
   that symbol indefinitely. General form: whenever a number is rounded
   before being sent somewhere that will reject it for being too large,
   round toward the safe side deliberately -- and prefer an API that takes
   no number at all.

29. **`open(path, 'w').write(open(path).read() + extra)` destroys
   the file: the truncating open is evaluated before the read.** Used this
   to append tests to an existing file and silently emptied it, keeping only
   the new content. Nothing raised -- the script printed success. It
   surfaced only because the suite total dropped (90 passed -> 74) while the
   new tests failed for unrelated-looking reasons, and it was recoverable
   only because the file happened to be committed. Read the whole file into
   a variable FIRST, then open for write; better still, write via a
   temporary file and rename. The wider lesson is the detection, not the
   gotcha: a test *count* falling is a signal in its own right, distinct
   from tests failing, and it is easy to miss while reading a list of
   failures. Check the total, not just the failures -- and treat any edit
   that both reads and writes the same path as a place to be careful.

30. **Fixing what triggered a bug is not the same as fixing the
   fragile assumption it exploited -- go back and guard the assumption
   too.** _reconcile_closed_positions took the most recently filled order
   to be a position's exit, without checking its side. The symbol-form
   mismatch (LESSONS 27/9) made a live position look closed, so the newest
   fill was its own BUY -- recorded as the exit, fabricating two wins and
   orphaning a real position with no stop. Fixing the symbol handling
   removed that particular route in, and it would have been easy to stop
   there: the observed bug was gone and the tests passed. But the
   assumption underneath was still unguarded, so any future path reaching
   that code with an entry as the newest fill would fail identically. Now
   the exit side is matched explicitly (a long closes with a sell, a short
   with a buy) and an unexplained disappearance leaves the trade open
   rather than inventing a price. After fixing a cause, ask what the
   broken code was ASSUMING, and whether anything still relies on that
   assumption holding.

31. **An uncovered function might not need tests -- check for callers
   before writing them.** A coverage audit flagged four untested functions
   in indicators.py. The instinct is to write four sets of tests; the
   right first move was to grep for callers. Two had none at all. Worse,
   one of them (macd) was still being computed by add_all_indicators into
   three columns on every bar, for every symbol, on every timeframe, and
   nothing ever read one of them -- wasted work in a loop that runs every
   60 seconds. Writing tests for it would have locked in dead code and
   made the coverage number look like reassurance. Deleting the two
   functions took indicators.py from 46% to 100% while REMOVING 15
   statements. Coverage measures what is tested, not what is needed: treat
   a gap as a question (why does nothing exercise this?) rather than an
   instruction to write a test.
