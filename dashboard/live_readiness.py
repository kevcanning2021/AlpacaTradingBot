"""Per-bot assessment of whether it is ready to trade real money.

Exists because "is it ready yet?" was being answered by judgement each time
it was asked, which is neither repeatable nor visible. These are the same
criteria used in that assessment, turned into something checkable.

Deliberately reports three states, not two. UNKNOWN is a first-class
result: a criterion that cannot be evaluated from available data must say
so rather than defaulting to pass (which would overstate readiness) or to
fail (which would understate it and train the reader to ignore the panel).
Main and Sofi record no entry timestamps, so their day-trade exposure is
genuinely unknowable from the journal -- that is an UNKNOWN, not a pass.

A bot is READY only when every criterion passes. Any FAIL or UNKNOWN means
not ready, because an unmeasured risk is not an absent one.
"""
import json
import logging
import os
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MIN_TRADES = 100          # sample needed before expectancy means much
MIN_FOR_STATS = 30        # below this, don't even report an expectancy
STABLE_DAYS_REQUIRED = 30  # consecutive days with no bug fix landing
# Trade COUNT alone is a crude proxy for evidence. 100 trades from a bot
# that trades 19x a week span about five weeks -- essentially one market
# regime -- while 100 from a bot trading 0.7x a week span two and a half
# years and many. Counting trades treats those as equivalent evidence when
# they plainly are not. Requiring a calendar span as well deliberately makes
# the bar HARDER for the fast bot, which is the honest direction: the fast
# bot is the one whose sample was being flattered.
MIN_SPAN_DAYS = 56  # 8 weeks -- more than one market mood, still reachable
PDT_EQUITY_FLOOR = 25_000  # US pattern-day-trader threshold
PDT_DAY_TRADES_ALLOWED = 3  # per rolling 5 business days below that floor

PASS, FAIL, UNKNOWN = 'pass', 'fail', 'unknown'


def _load_full_history(account_id, config):
    """Every closed trade, not the capped slice the Closed Trades panel uses.

    Returns (returns_as_fractions, (entry,exit) pairs, exit_times).

    Entry times are only available for Nova, which is what makes the others'
    PDT exposure unknowable -- but EXIT times exist everywhere, so the
    calendar span of the sample can be measured for all three.
    """
    if account_id in config.CLOSED_TRADES_PATHS:
        try:
            with open(config.CLOSED_TRADES_PATHS[account_id]) as f:
                history = json.load(f)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return [], [], []
        returns, exits = [], []
        for t in history:
            if t.get('pnl_pct') is not None:
                returns.append(t['pnl_pct'])
            try:
                exits.append(datetime.fromisoformat(t['timestamp']))
            except (KeyError, TypeError, ValueError):
                continue
        return returns, [], exits

    if account_id == 'trading2':
        try:
            with sqlite3.connect(f'file:{config.NOVA_JOURNAL_DB_PATH}?mode=ro', uri=True) as conn:
                rows = conn.execute(
                    'SELECT entry_time, exit_time, pnl_dollars, quantity, entry_price '
                    'FROM trades WHERE exit_time IS NOT NULL ORDER BY exit_time'
                ).fetchall()
        except (sqlite3.Error, OSError):
            return [], [], []
        returns, times, exits = [], [], []
        for entry_time, exit_time, pnl, qty, entry_price in rows:
            cost = (qty or 0) * (entry_price or 0)
            if cost and pnl is not None:
                returns.append(pnl / cost)
            try:
                times.append((datetime.fromisoformat(entry_time), datetime.fromisoformat(exit_time)))
                exits.append(datetime.fromisoformat(exit_time))
            except (TypeError, ValueError):
                continue
        return returns, times, exits

    return [], [], []


def _max_drawdown(returns):
    """Peak-to-trough of the compounding equity curve, as a fraction."""
    equity, peak, worst = 1.0, 1.0, 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return worst


def _days_since_last_fix(repo_path):
    """Days since a commit whose subject begins with 'Fix' landed.

    A proxy for code stability, and an imperfect one: it counts fixes that
    were found, which is not the same as bugs that exist. It is still the
    honest direction to be wrong in -- a bot with a fix landing today has
    demonstrably just had a bug, whatever else is true.
    """
    try:
        out = subprocess.run(
            ['git', '-C', repo_path, 'log', '-1', '--format=%cI', '--grep=^Fix'],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(out)).days
    except ValueError:
        return None


def _pdt_exposure(times):
    """Worst number of same-day round trips in any rolling 5-business-day
    window. Crypto is exempt from PDT, but entry/exit times alone can't
    identify asset class here, so this is deliberately the pessimistic
    reading -- it counts everything."""
    day_trades = [e.date() for e, x in times if e.date() == x.date()]
    if not day_trades:
        return 0
    counts = Counter(day_trades)
    days = sorted(counts)
    worst = 0
    for i, d in enumerate(days):
        window = [counts[o] for o in days[i:] if (o - d).days < 7]
        worst = max(worst, sum(window))
    return worst


def assess(account_id, config, repo_path=None):
    returns, times, exit_times = _load_full_history(account_id, config)
    n = len(returns)
    out = []

    out.append({
        'name': 'Sample size',
        'status': PASS if n >= MIN_TRADES else FAIL,
        'detail': f'{n} closed trades (need {MIN_TRADES})',
    })

    # Deliberately separate from sample size rather than folded into it, so
    # a bot that has the trades but not the elapsed time is visibly short on
    # the thing it is actually short on.
    if len(exit_times) >= 2:
        span_days = (max(exit_times) - min(exit_times)).days
        out.append({
            'name': 'Sample spans conditions',
            'status': PASS if span_days >= MIN_SPAN_DAYS else FAIL,
            'detail': f'{span_days} days from first to latest closed trade '
                       f'(need {MIN_SPAN_DAYS}; trades from one market mood are not '
                       f'independent evidence)',
        })
    else:
        out.append({'name': 'Sample spans conditions', 'status': UNKNOWN,
                    'detail': 'need at least two closed trades to measure a span'})

    if n < MIN_FOR_STATS:
        out.append({'name': 'Positive expectancy', 'status': UNKNOWN,
                    'detail': f'only {n} trades; need {MIN_FOR_STATS} before this means anything'})
        out.append({'name': 'Drawdown measured', 'status': UNKNOWN,
                    'detail': 'not enough trades to trace an equity curve'})
    else:
        exp = sum(returns) / n
        out.append({
            'name': 'Positive expectancy',
            'status': PASS if exp > 0 else FAIL,
            'detail': f'{exp*100:+.3f}% per trade over {n} trades',
        })
        dd = _max_drawdown(returns)
        out.append({
            'name': 'Drawdown measured',
            'status': PASS,
            'detail': f'worst peak-to-trough {dd*100:.1f}% (observed, not a limit)',
        })

    if times:
        worst = _pdt_exposure(times)
        ok = worst <= PDT_DAY_TRADES_ALLOWED
        out.append({
            'name': 'PDT clearance',
            'status': PASS if ok else FAIL,
            'detail': (f'{worst} day trades in the worst 5-day window; limit is '
                        f'{PDT_DAY_TRADES_ALLOWED} below ${PDT_EQUITY_FLOOR:,} equity'),
        })
    else:
        out.append({'name': 'PDT clearance', 'status': UNKNOWN,
                    'detail': 'no entry timestamps recorded, so day trades cannot be counted'})

    days = _days_since_last_fix(repo_path) if repo_path else None
    if days is None:
        out.append({'name': 'Code stability', 'status': UNKNOWN,
                    'detail': 'no fix history available'})
    else:
        out.append({
            'name': 'Code stability',
            'status': PASS if days >= STABLE_DAYS_REQUIRED else FAIL,
            'detail': f'{days} days since the last bug fix landed (need {STABLE_DAYS_REQUIRED})',
        })

    statuses = [c['status'] for c in out]
    if all(s == PASS for s in statuses):
        verdict = 'ready'
    elif any(s == FAIL for s in statuses):
        verdict = 'not ready'
    else:
        verdict = 'unproven'

    return {
        'verdict': verdict,
        'blocking': sum(s != PASS for s in statuses),
        'criteria': out,
    }
