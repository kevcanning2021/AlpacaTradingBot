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
