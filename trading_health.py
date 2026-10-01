"""Trading-health checks: is each bot actually working, not merely running?

Added 2026-09-30 at the account owner's request, after a week in which every one of
these was found by a human noticing, days late. Each is a real incident:

  - nova-main and nova-sofi auto-disabled their only setup and took ZERO stock
    trades for two days. Nothing alerted, because a bot that has stopped trading
    looks exactly like a bot with no signals.
  - nova-main was stopped out of META at 17:29 and re-entered the same setup at
    17:31, losing twice on one idea for -2.12R and $306.
  - four overnight gaps cost -24.2R before anyone split the record by hold span.
  - a single ETH position took half the account, because a notional-cap change
    meant for stocks silently applied to crypto too.

These are NOT advisory: they reach the phone. They are rare, specific, and each one
cost real money the first time.

They DETECT rather than auto-fix, deliberately. Re-enabling a disabled setup,
flattening a position after the close, or resizing a trade are trading decisions,
and a script that quietly makes those is far more dangerous than one that gets
ignored. Compare the stale-code restart in watchdog.py, which IS auto-fixed
precisely because it is one deterministic command with no judgement in it.
"""
import json
import os
import sqlite3
from datetime import datetime, time as dt_time, timedelta, timezone

BOT_JOURNALS = {
    'Nova': '/opt/trading-2-0/data/trade_journal.db',
    'Main': '/opt/nova-main/data/trade_journal.db',
    'Sofi': '/opt/nova-sofi/data/trade_journal.db',
}
SILENT_BOT_DAYS = 3           # calendar days with no ENTRY at all
OVERSIZED_LOSS_R = -2.0       # a stop that failed to cap the loss
RAPID_REENTRY_MINUTES = 60    # mirrors RiskConfig.reentry_cooldown_minutes

# When the re-entry cooldown actually went live. Re-entries BEFORE this are history
# the cooldown could not have prevented, and alerting that "the cooldown should have
# blocked this" about a time when it did not exist is simply a false alarm -- one
# that would have pinged the phone every 15 minutes for the 24-hour lookback.
# MOVE THIS if the cooldown is ever disabled and re-enabled.
COOLDOWN_LIVE_SINCE = '2026-09-30T20:22'


def _rows(db_path, sql, params=()):
    """Read-only journal query. Returns None when the journal is absent, so a bot
    that has not been set up yet is silence rather than a false alarm."""
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _parse(ts):
    """Journal timestamps exist both with and without a timezone. A naive value
    compared against an aware now() raises, which inside a check would take the
    whole watchdog cycle down."""
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def check_silent_bots(now=None):
    """A bot that has stopped opening trades.

    Checks the SYMPTOM, not the cause. A disabled setup, a starving trend gate, a
    blocked risk gate and a stuck scanner all look identical from here -- and all of
    them matter equally, so detecting the silence catches every one, including the
    ones not yet thought of.
    """
    now = now or datetime.now(timezone.utc)
    issues = []
    for label, db in BOT_JOURNALS.items():
        rows = _rows(db, 'SELECT MAX(entry_time) AS t FROM trades')
        if not rows:
            continue
        newest = (rows[0] or {}).get('t')
        if not newest:
            continue
        parsed = _parse(newest)
        if parsed is None:
            continue
        days = (now - parsed).days
        if days >= SILENT_BOT_DAYS:
            issues.append((
                'no_trades:%s' % label,
                '[%s] has opened NO trade for %d days (last entry %s). A bot that has '
                'stopped trading looks identical to one with no signals -- check whether '
                'its setup is disabled, the risk gate is blocking, or the scanner is stuck.'
                % (label, days, newest[:16])))
    return issues


def check_oversized_losses(now=None):
    """A loss worse than 2R means the stop did not do its job -- it gapped through,
    or the exit path never fired. Either is worth knowing the same day."""
    now = now or datetime.now(timezone.utc)
    issues = []
    since = (now - timedelta(hours=24)).isoformat()
    for label, db in BOT_JOURNALS.items():
        rows = _rows(db,
                     'SELECT symbol, entry_time, pnl_r, pnl_dollars FROM trades '
                     'WHERE pnl_r IS NOT NULL AND pnl_r < ? AND exit_time > ? ORDER BY pnl_r',
                     (OVERSIZED_LOSS_R, since))
        for r in (rows or []):
            issues.append((
                'oversized_loss:%s:%s:%s' % (label, r['symbol'], (r['entry_time'] or '')[:16]),
                '[%s] %s lost %.2fR ($%.2f) -- the stop did not cap it at 1R. Either it '
                'gapped through, or the exit path did not fire.'
                % (label, r['symbol'], r['pnl_r'], r['pnl_dollars'] or 0.0)))
    return issues


def check_rapid_reentry(now=None):
    """Re-entering a symbol that has just stopped you out. Exactly what cost nova-main
    -2.12R on META in ten minutes on 2026-09-30, before the cooldown existed."""
    now = now or datetime.now(timezone.utc)
    issues = []
    since = (now - timedelta(hours=24)).isoformat()
    for label, db in BOT_JOURNALS.items():
        rows = _rows(db,
                     'SELECT symbol, entry_time, exit_time, outcome FROM trades '
                     'WHERE entry_time > ? ORDER BY symbol, entry_time', (since,))
        by_symbol = {}
        for r in (rows or []):
            by_symbol.setdefault(r['symbol'], []).append(r)
        for sym, seq in by_symbol.items():
            for prev, nxt in zip(seq, seq[1:]):
                if prev.get('outcome') != 'loss':
                    continue  # only a LOSS starts a cooldown; re-entry after a win is fine
                if (nxt.get('entry_time') or '') < COOLDOWN_LIVE_SINCE:
                    continue  # predates the cooldown; not something it could have stopped
                a, b = _parse(prev.get('exit_time')), _parse(nxt.get('entry_time'))
                if a is None or b is None:
                    continue
                gap = (b - a).total_seconds() / 60.0
                if 0 <= gap < RAPID_REENTRY_MINUTES:
                    issues.append((
                        'rapid_reentry:%s:%s:%s' % (label, sym, (nxt['entry_time'] or '')[:16]),
                        '[%s] re-entered %s %.0f minutes after it stopped out -- the re-entry '
                        'cooldown should have blocked this. Check REENTRY_COOLDOWN_MINUTES.'
                        % (label, sym, gap)))
    return issues


def check_positions_after_close(account_key, label, client, now=None):
    """Any STOCK position still open well after the close means the flatten policy
    did not do its job, and an unflattened position carries exactly the overnight gap
    exposure that cost -24.2R. Crypto is exempt -- it has no close.

    Windowed to 20:30-23:00 UTC: after the 20:00 close and its flatten window, and
    before the next session could legitimately open anything.
    """
    now = now or datetime.now(timezone.utc)
    if not (dt_time(20, 30) <= now.time() <= dt_time(23, 0)):
        return []
    try:
        stocks = [p for p in client.get_positions()
                  if p.get('asset_class') != 'crypto' and '/' not in (p.get('symbol') or '')]
    except Exception as e:
        return [('watchdog_internal_error:after_close:%s' % account_key,
                 'could not check post-close positions for %s: %s' % (label, e))]
    if not stocks:
        return []
    names = ', '.join('%s %s' % (p.get('symbol'), p.get('qty')) for p in stocks)
    return [('unflattened:%s' % account_key,
             '[%s] still holds STOCK after the close: %s. The flatten policy did not '
             'close these, so they carry overnight gap risk -- the exact exposure that '
             'cost -24.2R before flattening was added.' % (label, names))]


def check_all(now=None):
    """Every journal-based check. Each is isolated so one failure cannot take the
    others down with it."""
    issues = []
    for fn in (check_silent_bots, check_oversized_losses, check_rapid_reentry,
               check_approaching_setup_disable, check_config_drift):
        try:
            issues += fn(now=now)
        except Exception as e:
            issues.append(('watchdog_internal_error:%s' % fn.__name__,
                           'trading-health check %s crashed: %s' % (fn.__name__, e)))
    return issues


# ---------------------------------------------------------------------------
# Added 2026-10-01 after the account owner said, correctly, that he still has to
# check because too much gets missed. Each gap below had already bitten once.

SETUP_MIN_TRADES = 15          # mirrors TradeJournal.is_setup_disabled
SETUP_MIN_AVG_R = -0.1         # mirrors TradeJournal.is_setup_disabled
SETUP_WARN_AT = 10             # warn while there is still time to react
DRAWDOWN_ALERT_PCT = 2.0       # from the high-water mark, per account
WATERMARK_PATH = "/opt/alpaca-bot-test/equity_watermark.json"

BOT_ENVS = {
    "Nova": "/opt/trading-2-0/.env",
    "Main": "/opt/nova-main/.env",
    "Sofi": "/opt/nova-sofi/.env",
}

# What each bot is SUPPOSED to be running, as decided 2026-09-29/30. A silent
# revert of any of these is invisible otherwise -- the crypto side effect that put
# half of Main into one ETH trade was exactly this shape, a setting reaching further
# than intended with nothing watching.
EXPECTED_ENV = {
    "Main": {"USE_BROKER_BRACKETS": "true", "CRYPTO_WATCHLIST": "",
             "MAX_POSITION_NOTIONAL_PCT": "0.25", "MAX_CONCURRENT_POSITIONS": "4"},
}


def _env_values(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _setup_since(env_path):
    return _env_values(env_path).get("SETUP_STATS_SINCE", "2026-09-28T18:00")


def check_approaching_setup_disable(now=None):
    """Warn BEFORE a bot switches itself off, not three days after.

    is_setup_disabled trips at 15 trades with avgR below -0.1, and when it trips the
    bot simply stops trading -- which is then only caught by check_silent_bots, after
    three days of silence. On 2026-09-30 that cost nova-main two full days. A warning
    at 10 trades leaves room to look at why before it happens.
    """
    issues = []
    for label, db in BOT_JOURNALS.items():
        since = _setup_since(BOT_ENVS.get(label, ""))
        rows = _rows(db,
                     "SELECT setup_type, COUNT(*) n, AVG(pnl_r) avg_r FROM trades "
                     "WHERE outcome != ? AND entry_time > ? GROUP BY setup_type",
                     ("open", since))
        for r in (rows or []):
            n, avg_r = r["n"], r["avg_r"] or 0.0
            if n >= SETUP_MIN_TRADES or n < SETUP_WARN_AT or avg_r >= SETUP_MIN_AVG_R:
                continue
            issues.append((
                "setup_near_disable:%s:%s" % (label, r["setup_type"]),
                "[%s] %s is %d trades from switching itself OFF: n=%d, avgR %+.3f "
                "against a %.1f threshold at %d trades. When it trips the bot stops "
                "trading entirely and nothing notices for days."
                % (label, r["setup_type"], SETUP_MIN_TRADES - n, n, avg_r,
                   SETUP_MIN_AVG_R, SETUP_MIN_TRADES)))
    return issues


def check_config_drift(now=None):
    """Has a bot quietly stopped running the configuration it is supposed to?"""
    issues = []
    for label, expected in EXPECTED_ENV.items():
        path = BOT_ENVS.get(label)
        if not path or not os.path.exists(path):
            continue
        actual = _env_values(path)
        for key, want in expected.items():
            got = actual.get(key)
            if got is None:
                issues.append(("config_drift:%s:%s" % (label, key),
                               "[%s] %s is MISSING from its .env; expected %r. The "
                               "default will apply instead." % (label, key, want)))
            elif got != want:
                issues.append(("config_drift:%s:%s" % (label, key),
                               "[%s] %s is %r but should be %r." % (label, key, got, want)))
    return issues


def check_drawdown(account_key, label, client, now=None):
    """Equity against its own high-water mark. Nothing watched this before, so the
    $878 day on 2026-09-30 raised no flag at all."""
    try:
        acct = client.get_account()
        equity = float(acct.get("equity"))
    except Exception as e:
        return [("watchdog_internal_error:drawdown:%s" % account_key,
                 "could not read equity for %s: %s" % (label, e))]
    marks = {}
    if os.path.exists(WATERMARK_PATH):
        try:
            with open(WATERMARK_PATH, encoding="utf-8") as fh:
                marks = json.load(fh)
        except Exception:
            marks = {}
    peak = max(float(marks.get(account_key, 0.0)), equity)
    marks[account_key] = peak
    try:
        with open(WATERMARK_PATH, "w", encoding="utf-8") as fh:
            json.dump(marks, fh, indent=2)
    except Exception:
        pass
    if peak <= 0:
        return []
    dd = (peak - equity) / peak * 100.0
    if dd < DRAWDOWN_ALERT_PCT:
        return []
    return [("drawdown:%s" % account_key,
             "[%s] equity $%.2f is %.2f%% below its high-water mark of $%.2f. "
             "Nothing else watches account-level loss -- the per-trade checks only "
             "see individual trades." % (label, equity, dd, peak))]
