"""Standalone VPS watchdog: checks service health, log errors, git drift, and
account state across all live Alpaca accounts on this VPS (Production, Test,
SOFI, Trading 2.0); sends a Telegram alert only when something's actually
wrong.

Runs from /opt/alpaca-bot-test via crontab every 15 min on the VPS itself,
independent of any Claude Code session -- see KNOWN_LIMITATIONS.md / project
notes for why this exists (a session-only health check dies when the
session/laptop does). Originally Test-account-only; extended 2026-08-25
after a production API-key outage (and, separately, a brief production
401 blip) both went unnoticed until manually checked -- this account's own
.env only ever held this account's own credentials, so the other bots were
invisible to it. Extended again 2026-08-26 to add Trading 2.0 (a separate,
isolated multi-timeframe bot on the same VPS, not part of this repo).
Cross-account read access uses the same ALPACA_PROD_*/ALPACA_SOFI_*/
ALPACA_TRADING2_* env var naming the dashboard already established for the
same purpose (see dashboard/config.py).

Extended again 2026-08-31 with check_git_drift(), absorbing the one
deterministic-checkable capability from /opt/fleet-review-agent (a Claude
tool-use loop) after the user decided not to keep funding ANTHROPIC_API_KEY
credits. That component's real value was LLM reasoning (catching
cross-file-consistency bugs like the 2026-08-27 SKIP_FRIDAYS/scheduler.py
drift, or the 2026-08-29 strategy_check.py cross-account credential bug) --
this watchdog can't replicate that, only the "is anything sitting uncommitted"
part. Its cron entry was removed; the code is left in place untouched as
historical record, same treatment as every other retired component on this
box (Mini, Watcher, the old Telegram bot). Research Agent veto
(RESEARCH_AGENT_VETO_ENABLED) was disabled the same day on all 3 bots for
the same reason -- it was already a functional no-op under fail-open once
credits ran out, so disabling it changed no actual trading behavior. Since
superseded 2026-09-02: replaced with a free, zero-cost keyword-based check
(agents/research_agent.py / bot/research_agent.py) with no Anthropic
dependency, and re-enabled on all 3 bots. See check_research_agent_health()
below, added 2026-09-05, for this watchdog's own visibility into it.

State is kept in a small JSON file so:
- the same issue doesn't re-alert every 15 min (only on first detection, then
  again every ALERT_COOLDOWN_SECONDS if it's still unresolved)
- log-error scanning only looks at genuinely new journal lines since last run
  (tracked per service now, not a single global timestamp)
- already-known/explained orders (see KNOWN_MANUAL_ORDER_IDS) don't re-fire
"""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient
from telegram_notifier import TelegramNotifier
from config import settings

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchdog_state.json')
SERVICES = [
    # alpaca-bot-test.service intentionally excluded: retired 2026-08-27, its
    # account reassigned to trading-2-0 (Alpaca caps free accounts at 3 paper
    # accounts). alpaca-telegram-bot.service also excluded: retired the same
    # day, redundant with the dashboard once it correctly separated
    # Main/Sofi/Nova (it had drifted to silently reporting Nova's data under
    # a still-"test account"-labeled bot). pdt15rev-bot.service excluded:
    # retired 2026-09-02 -- its own strategy hardly ever traded (single
    # symbol, once-per-day setup window); the SOFI account was repurposed
    # for sofi-bot.service instead (a clone of Main's validated dual-signal
    # scanner, different watchlist for real diversification). All three are
    # deliberately stopped+disabled -- alerting on any being inactive would
    # just be noise.
    # alpaca-bot.service retired 2026-09-23 (stopped + disabled; nothing
    # deleted, re-enable with `systemctl enable --now alpaca-bot`). Main's
    # dual-signal scanner returned +0.07% over its whole life, while Nova's
    # strategy returned +9.7% on a $50 account it can never scale past -- the
    # PDT day-trade limit only binds under $25k. nova-main.service now runs
    # Nova's code against Main's ~$100k paper account to find out whether that
    # edge is real at size, or was just three lucky overnight META gaps.
    # Leaving alpaca-bot in this list would alert every run now it is off.
    'nova-main.service', 'alpaca-dashboard.service',
    'sofi-bot.service', 'trading-2-0.service',
]
# Units whose running process is compared against their repo's newest CODE
# commit -- see check_stale_code(). Deliberately separate from SERVICES (which
# answers "is it up?") and GIT_REPOS (which answers "is the work committed?");
# neither of those can see the gap this closes.
SERVICE_REPOS = {
    'trading-2-0.service': '/opt/trading-2-0',
    'sofi-bot.service': '/opt/sofi-bot',
    'nova-main.service': '/opt/nova-main',
    'alpaca-dashboard.service': '/opt/alpaca-dashboard',
}

# A deploy legitimately spends a few moments with HEAD newer than the running
# process (commit, then restart). Only complain once that gap has outlived a
# plausible deploy -- one watchdog cycle.
STALE_CODE_GRACE_MINUTES = 15

ALERT_COOLDOWN_SECONDS = 2 * 60 * 60


# Advisory alerts are written here instead of being sent to the user's phone.
# The agent sweep reads this; the user should not be the fleet's error
# reporting channel. Urgent alerts still go to Telegram AND land here.
AGENT_REVIEW_QUEUE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   'agent_review_queue.jsonl')


# A downed service is fixed by the agent sweep within ~15 min. Escalating on
# FIRST detection would put an error in front of the user that was about to be
# resolved without them -- which is exactly the thing they asked to stop
# happening. Three watchdog cycles is long enough that if it is still down,
# automation has genuinely failed and a human is the right next step.
ESCALATE_AFTER_MINUTES = 45


def is_advisory(key):
    """True for alert classes that never warrant waking the user.

    These announce ONCE to the agent queue while their message is unchanged,
    rather than re-pinging every ALERT_COOLDOWN_SECONDS. They stay in
    active_alerts and on the dashboard throughout -- visibility is not what is
    being reduced, repetition is.

    Why (2026-09-24): four stuck_veto alerts were live at once, three of them
    for entirely correct vetoes, which at a 2h cooldown is ~48 identical
    Telegram messages a day needing no action. The user reported "lots of
    errors"; the fleet was healthy. An alert channel that cries wolf hourly is
    worse than one that stays silent, because the one message that matters
    arrives looking exactly like the forty-seven that did not.

    bot_order belongs here despite not being an error at all: it fires on
    EVERY order the bot places, and with three bots trading that is pure
    volume. Normal trading activity belongs on the dashboard, not in an alert
    channel. watchdog_internal_error belongs here because a broken check is
    the agent's problem to fix, not the account owner's to be told about.
    """
    return (key.startswith(('log_errors:', 'git_drift:', 'stale_code:',
                             'watchdog_internal_error:'))
            or ':stuck_veto:' in key
            or ':bot_order:' in key
            or ':api_error:' in key
            or ':research_agent_read_error' in key
            or ':not_configured' in key)


def alert_reaches_user(key, first_seen=None, now=None):
    """Whether this alert goes to Telegram, or only to the agent queue.

    Deliberately narrow. The standing instruction from the user (2026-09-24)
    is that they should never be the fleet's error-reporting channel: errors
    are the agent's to fix or to carry. What survives here is only what a
    human genuinely has to decide or act on, and what cannot wait for the next
    agent sweep.

    - Credentials and unattributed orders: a leaked key or an order that did
      not come from this account's own key cannot wait, and no amount of
      automatic restarting addresses either.
    - A breached stop is money moving the wrong way right now.
    - A downed service escalates only after ESCALATE_AFTER_MINUTES, giving the
      sweep time to restart it first. If it is still down by then, restarting
      is not the answer and a human is.
    """
    if (key.startswith(('leaked_credential:', 'secrets_hygiene:'))
            or ':unattributed_order:' in key
            or ':stop_loss_breach:' in key):
        return True
    if key.startswith('service_down:'):
        if not first_seen:
            return False
        try:
            age_min = (now - datetime.fromisoformat(first_seen)).total_seconds() / 60
        except (TypeError, ValueError, AttributeError):
            return False
        return age_min >= ESCALATE_AFTER_MINUTES
    return False


# How far back a log error keeps an alert alive. Added 2026-09-21 after a
# week-long miss: check_new_log_errors only ever inspected the journal since
# the previous run (~15 min) and main() deletes any alert key not re-raised
# on the next run, so this alert class was transient by construction. Nova
# logged 51 failed crypto orders across five days and every snapshot read of
# active_alerts correctly showed zero -- spread over ~768 runs the alert was
# live perhaps 3-4% of the time. The Telegram pings all fired, so the human
# watching a phone had strictly better information than anything reading the
# state file, which is backwards. An alert meant to be noticed by something
# that polls has to outlive the instant that raised it.
# 2h, reduced from 24 on 2026-09-24. The original reasoning still stands but
# the constraint behind it is gone.
#
# 24h was set after a week-long miss: this alert class was transient by
# construction (detection window ~15 min, and main() deleted any key not
# re-raised), so Nova's 51 failed crypto orders across five days were live in
# active_alerts perhaps 3-4% of the time. Anything polling the state file had
# a ~96% chance of seeing nothing. Holding the alert open for 24h fixed that.
#
# What changed: advisory alerts are now ALSO appended to
# AGENT_REVIEW_QUEUE, which the sweep drains and deletes. The durable record
# of "this happened" lives there, so active_alerts no longer has to be both
# the live state AND the history. Keeping a 24h window made the dashboard
# show a recovered blip as an open error for a full day, which is the
# complaint this addresses.
#
# 2h still spans several watchdog runs, so a genuinely intermittent fault
# stays visible between occurrences rather than flickering.
LOG_ERROR_WINDOW_HOURS = 2

# Repos checked for uncommitted drift. The note that used to sit here said
# trading-2-0 was "deliberately absent -- a plain copied directory, not a git
# repo". That was untrue by 2026-09-23 (it has its own .git and a GitHub
# remote, and is committed to constantly), and because the omission carried a
# confident-sounding justification, nobody re-checked it. A comment explaining
# why something is missing ages into the reason it stays missing.
# pdt15rev-bot removed 2026-09-02 (retired, see SERVICES comment) -- its repo
# is left untouched as historical record but nothing will commit to it again,
# so drift-checking it would just be permanent noise. sofi-bot added in its
# place (a separate clone of AlpacaTradingBot:production, own .git).
GIT_REPOS = {
    'alpaca-bot': '/opt/alpaca-bot',
    'sofi-bot': '/opt/sofi-bot',
    # trading-2-0 (Nova) added 2026-09-23. It had been missing since this
    # check was written, which meant the single most actively developed repo
    # in the fleet was the one repo whose uncommitted work was never flagged
    # -- while 'fleet-review-agent', retired 2026-08-31 and unchanged since,
    # was still being watched. Exactly backwards, and silent about it because
    # both paths exist so nothing ever errored.
    'trading-2-0': '/opt/trading-2-0',
    'alpaca-dashboard': '/opt/alpaca-dashboard',
    'alpaca-bot-test': '/opt/alpaca-bot-test',
}

# Added 2026-09-05 after rotating a real exposed Nova API key/secret (an
# unrelated dataclass repr in a test failure printed both into a Claude
# session's own output -- not this VPS -- but the same failure mode is
# entirely possible here too: any future exception or log line that reprs a
# config object embeds the real credential). Two independent checks, in
# check_secrets_hygiene() and check_new_log_errors() below, built generally
# rather than treating this as a one-off. trading-2-0 has no git repo on
# this box (see GIT_REPOS comment above) so it only gets the permission
# check, not the git-tracked check.
ENV_FILES = {
    'alpaca-bot': '/opt/alpaca-bot/.env',
    'sofi-bot': '/opt/sofi-bot/.env',
    'alpaca-dashboard': '/opt/alpaca-dashboard/dashboard/.env',
    'alpaca-bot-test': '/opt/alpaca-bot-test/.env',
    'trading-2-0': '/opt/trading-2-0/.env',
}

# Alpaca key IDs are PK + 26 uppercase-alphanumeric chars -- a distinctive
# enough shape that a match in a log line is a real credential, not a false
# positive off an order/asset UUID (which has dashes) or a price/symbol.
# The secret pattern requires "secret" to appear nearby on the same line
# (case-insensitive) rather than matching any 40-44 char alnum string on its
# own, which would false-positive constantly (order IDs, hashes, etc.) --
# this still catches both the literal .env line shape (SECRET_KEY_X=...) and
# a Python repr (secret_key='...'), which is exactly the real incident.
_LEAKED_KEY_ID_PATTERN = re.compile(r'\bPK[A-Z0-9]{26}\b')
_LEAKED_SECRET_PATTERN = re.compile(r"(?i)secret[a-z_]*\s*[=:]\s*['\"]?([A-Za-z0-9+/]{40,44})\b")

# Accounts this watchdog checks positions/orders for, and whose service log
# gets scanned for new tracebacks/ERROR lines. Test's own credentials come
# from this service's own .env (config.settings, same as trader.py uses);
# Production's and SOFI's are separate read-access-only-in-practice
# credentials (Alpaca has no read-only key type, but every call made with
# them here is a GET) added to this service's .env specifically for this
# watchdog.
ACCOUNTS = {
    'production': {
        'label': 'Production',
        # Same Alpaca account; the bot trading it changed 2026-09-23 from
        # alpaca-bot.service (retired) to nova-main.service. Left pointing at
        # the dead unit, this would scan a journal that never gets another
        # line -- i.e. report perfect health forever.
        'log_unit': 'nova-main.service',
        'api_key': os.getenv('ALPACA_API_KEY_MAIN', ''),
        'secret_key': os.getenv('ALPACA_SECRET_KEY_MAIN', ''),
    },
    # 'test' account intentionally removed 2026-08-27: alpaca-bot-test was
    # retired and its account (whose credentials still live in this service's
    # own settings.ALPACA_API_KEY/SECRET_KEY, unchanged) was reassigned to
    # trading-2-0 -- see the 'trading2' entry below, which now points at the
    # same account under its correct current label.
    # Same account/credentials as always -- only the bot running against it
    # changed 2026-09-02 (pdt15rev-bot retired -> sofi-bot, see SERVICES).
    'sofi': {
        'label': 'SOFI',
        'log_unit': 'sofi-bot.service',
        'api_key': os.getenv('ALPACA_API_KEY_SOFI', ''),
        'secret_key': os.getenv('ALPACA_SECRET_KEY_SOFI', ''),
    },
    'trading2': {
        'label': 'Trading 2.0',
        'log_unit': 'trading-2-0.service',
        'api_key': os.getenv('ALPACA_API_KEY_NOVA', ''),
        'secret_key': os.getenv('ALPACA_SECRET_KEY_NOVA', ''),
    },
}

# Local file paths, not API credentials -- these three bots' own research
# agents each write their decision history to a JSON file on this same VPS
# (same paths dashboard/config.py reads for its decision-history panel), so
# no cross-account auth is needed to read them, just the path.
RESEARCH_AGENT_DECISIONS_PATHS = {
    'production': '/opt/nova-main/data/research_decisions.json',  # nova-main since 2026-09-23
    'sofi': '/opt/sofi-bot/agent_decisions_state.json',
    'trading2': '/opt/trading-2-0/data/research_decisions.json',
}

# Added 2026-09-05 after a real live miss: a DOJ beef-pricing-probe roundup
# article, undercounted by agents/research_agent.py's own multi-company
# filter (see that file's _OTHER_COMPANIES_PATTERN fix), vetoed SOFI's COST
# six consecutive hourly checks before the user spotted it on the dashboard
# -- nothing on this VPS was watching the research agent's own decisions for
# a stuck pattern, only whether the bots were running and trading. This
# doesn't judge whether a veto is right or wrong (this project's keyword
# checker was never meant to have that nuance -- see research_agent.py's own
# docstring) -- it just surfaces "the identical reasoning fired N times in a
# row" for a human to glance at, the same non-judgmental way stuck_sell in
# strategy_check.py surfaces a persistent SELL signal without claiming it's
# wrong. A genuine, still-fresh red flag (e.g. Nova's real MSFT revenue
# restatement, correctly vetoed 3 times running) will also trip this --
# that's fine, it's meant to be reviewed either way, not auto-resolved.
STUCK_VETO_THRESHOLD = 3

# A symbol a bot stops re-checking entirely would otherwise sit at the tail
# of its own decision history forever and keep alerting on days-old activity
# as if it were still live -- gates check_research_agent_health() on the most
# recent decision's own age. Matches research_agent.py's LOOKBACK_HOURS=72,
# the same window that module already treats as still-relevant news.
# 6h, not 72. This is the answer to "is this symbol being blocked RIGHT NOW",
# and the old value answered a different question.
#
# 72 was chosen to mirror research_agent.py's LOOKBACK_HOURS -- how old an
# ARTICLE may be and still count. That is not the same as how old a DECISION
# may be and still mean something is stuck. A genuinely stuck veto re-fires
# constantly: TSLA logged 751 consecutive identical vetoes in 23.9h, roughly
# one every two minutes. So a decision more than a few hours old is not a
# block, it is history.
#
# Measured 2026-09-24, with the old value in place: TSLA's last decision was
# 0.2h old (really blocking) while SPY's was 45h and ETH/USD's 50h. All three
# were alerting identically. The SPY one had been fixed in code two days
# earlier and kept reappearing on the dashboard anyway -- a sweep cleared it
# and the next run re-derived it from the same stale history, because the
# check reads decision records rather than asking whether the veto still
# holds. That is what "errors don't close when they're resolved" looked like
# from the inside.
#
# 6h is generous for the real case (a symbol checked a few times a day still
# qualifies) and short enough that a fixed cause clears within the session
# rather than two days later.
STUCK_VETO_MAX_AGE_HOURS = 6

# Maps an issue key back to which bot it's actually about, so the dashboard can
# group issues per-bot instead of one flat list -- added 2026-09-02 after the
# user pointed out a flat list makes it hard to tell which bot an issue belongs
# to at a glance. Uses the same user-facing nicknames as dashboard/accounts.py
# (Main/Sofi/Nova), not this file's own internal ACCOUNTS labels
# (Production/SOFI/Trading 2.0), so the grouping matches what's shown
# everywhere else on the dashboard. Everything not tied to a specific trading
# bot (this watchdog's own repo, the dashboard's own service/repo, the retired
# fleet-review-agent) groups under 'Infra'.
SERVICE_TO_BOT = {
    'alpaca-bot.service': 'Main',
    'sofi-bot.service': 'Sofi',
    'trading-2-0.service': 'Nova',
    'alpaca-dashboard.service': 'Infra',
}
REPO_TO_BOT = {
    'alpaca-bot': 'Main',
    'sofi-bot': 'Sofi',
    'alpaca-dashboard': 'Infra',
    'alpaca-bot-test': 'Infra',
    'fleet-review-agent': 'Infra',
    'trading-2-0': 'Nova',  # not a git repo (see GIT_REPOS), only used by check_secrets_hygiene's ENV_FILES keys
}
ACCOUNT_KEY_TO_BOT = {
    'production': 'Main',
    'sofi': 'Sofi',
    'trading2': 'Nova',
}


def bot_for_key(key: str) -> str:
    prefix = key.split(':', 1)[0]
    if prefix == 'service_down' or prefix == 'log_errors' or prefix == 'leaked_credential':
        return SERVICE_TO_BOT.get(key.split(':', 1)[1], 'Infra')
    if prefix == 'git_drift' or prefix == 'secrets_hygiene':
        return REPO_TO_BOT.get(key.split(':')[1], 'Infra')
    if prefix == 'watchdog_internal_error':
        parts = key.split(':')
        if len(parts) >= 3 and parts[1] in ('check_account', 'check_new_log_errors'):
            return ACCOUNT_KEY_TO_BOT.get(parts[2], 'Infra')
        return 'Infra'
    return ACCOUNT_KEY_TO_BOT.get(prefix, 'Infra')

# Orders already investigated and confirmed not to be bugs â€” don't re-flag them.
# Order IDs are UUIDs (globally unique regardless of which account placed them),
# so one flat set safely covers all three accounts.
KNOWN_MANUAL_ORDER_IDS = {
    'e9b313b1-7668-45f2-8544-b7bb0cc83cd2',  # 2026-07-10 rebalance, placed manually from dev machine
    '47a7e888-f544-4533-a471-8c1bdb05b7b4',  # 2026-07-13 accidental AAPL close_position() call
                                              # while unit-testing threshold logic from dev machine
                                              # against the real client instead of a stub â€” see
                                              # project memory Lesson #7. +1.7% gain, no harm; not a
                                              # bot trade, so trader.py's streak tracking correctly
                                              # never saw it.
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
    else:
        state = {}
    state.setdefault('active_alerts', {})
    state.setdefault('seen_order_ids', [])
    # Was a single global ISO string pre-2026-08-25; now per-service-unit so each
    # account's log gets its own "since last run" cursor. A stale string from the
    # old format is discarded rather than misapplied to a new unit -- worst case,
    # the first post-upgrade run does a -n 50 fallback per unit instead of --since,
    # which is harmless (just re-scans a bit of already-seen log).
    last_log_check = state.get('last_log_check')
    if not isinstance(last_log_check, dict):
        last_log_check = {}
    state['last_log_check'] = last_log_check

    # Rolling record of which runs saw log errors, per unit, so a recurring
    # fault stays visible between occurrences -- see LOG_ERROR_WINDOW_HOURS.
    error_log = state.get('log_error_log')
    if not isinstance(error_log, dict):
        error_log = {}
    state['log_error_log'] = error_log
    return state


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def queue_for_agent(entries, now=None):
    """Append alerts to the agent review queue (never raises).

    Deliberately best-effort: a failure to write this file must not stop the
    Telegram path or lose the state save. An unwritten queue line costs one
    missed review; an exception here would cost the whole watchdog run.
    """
    if not entries:
        return
    now = now or datetime.now(timezone.utc)
    try:
        with open(AGENT_REVIEW_QUEUE, 'a') as f:
            for severity, key, message in entries:
                f.write(json.dumps({
                    'ts': now.isoformat(),
                    'severity': severity,
                    'key': key,
                    'bot': bot_for_key(key),
                    'message': message,
                }) + chr(10))
    except OSError as e:
        print(f"queue_for_agent failed: {e}")


def _enabled_states():
    """{unit: is-enabled state} for SERVICES, in one subprocess call.

    Like is-active, systemctl is-enabled accepts multiple units and prints one
    line per unit in order, regardless of exit code.
    """
    try:
        result = subprocess.run(['systemctl', 'is-enabled'] + SERVICES,
                                 capture_output=True, text=True, timeout=15)
    except (subprocess.SubprocessError, OSError):
        return {}
    return dict(zip(SERVICES, result.stdout.splitlines()))


def check_services():
    # systemctl is-active accepts multiple units and prints one status line per unit,
    # in the given order, regardless of exit code -- one subprocess spawn instead of
    # one per service (confirmed live: a mix of active/inactive units still lists all
    # statuses in order).
    #
    # A DISABLED unit that is not running is correct, not a fault. `disabled`
    # means a person deliberately retired it, and alerting on that fights the
    # decision rather than reporting a problem. Added 2026-09-24 after exactly
    # that: the user ran `systemctl disable --now sofi-bot` as a planned
    # switchover, an automated sweep read "inactive" as a crash and restarted
    # it 78 seconds later, and this check would then have escalated the
    # re-stopped unit to their phone 45 minutes on -- the fleet arguing with
    # its owner in two different ways about one decision they had already made.
    #
    # This also replaces the hand-maintained exclusion list that used to carry
    # alpaca-bot-test, alpaca-telegram-bot and pdt15rev-bot: every one of those
    # is stopped AND disabled, so the state of the unit now says what a comment
    # used to have to. A list of exceptions rots (see LESSONS 26); asking the
    # system is self-maintaining.
    issues = []
    result = subprocess.run(['systemctl', 'is-active'] + SERVICES, capture_output=True, text=True)
    statuses = result.stdout.splitlines()
    enabled = _enabled_states()
    for svc, status in zip(SERVICES, statuses):
        if status == 'active':
            continue
        if enabled.get(svc) == 'disabled':
            continue  # deliberately retired -- see above
        issues.append((f'service_down:{svc}', f'{svc} is "{status}", not active'))
    return issues


def check_new_log_errors(unit, since_iso, error_log=None, now=None):
    """Detection is unchanged -- errors in the journal since the previous run
    -- but occurrences are now recorded in error_log so the alert survives
    the quiet gaps between them (see LOG_ERROR_WINDOW_HOURS). error_log is
    mutated in place and persisted in watchdog_state.json; passing None keeps
    the old stateless behaviour, which the leaked-credential scan below still
    relies on.
    """
    issues = []
    now = now or datetime.now(timezone.utc)
    cmd = ['journalctl', '-u', unit, '--no-pager', '-o', 'cat']
    if since_iso:
        cmd += ['--since', since_iso]
    else:
        cmd += ['-n', '50']
    result = subprocess.run(cmd, capture_output=True, text=True)
    lines = result.stdout.splitlines()
    bad_lines = [l for l in lines if 'Traceback' in l or 'ERROR' in l]

    if error_log is None:
        # Stateless fallback: original behaviour, alert only on a fresh hit.
        if bad_lines:
            sample = '\n'.join(bad_lines[-5:])
            issues.append((f'log_errors:{unit}', f'New errors in {unit} log:\n{sample}'))
    else:
        entry = error_log.setdefault(unit, {'events': [], 'latest_sample': ''})
        if bad_lines:
            # One event per run that saw errors, not one per line -- keeps the
            # list bounded (at most 96/day) while still counting recurrences.
            entry['events'].append(now.isoformat())
            entry['latest_sample'] = '\n'.join(bad_lines[-5:])
        cutoff = now - timedelta(hours=LOG_ERROR_WINDOW_HOURS)
        kept = []
        for ts in entry['events']:
            try:
                if datetime.fromisoformat(ts) > cutoff:
                    kept.append(ts)
            except ValueError:
                continue  # unparseable timestamp from an older state file
        entry['events'] = kept
        if kept:
            first = min(kept)
            issues.append((
                f'log_errors:{unit}',
                f'{len(kept)} run(s) with errors in {unit} in the last '
                f'{LOG_ERROR_WINDOW_HOURS}h (first {first[:16]}, latest '
                f'{max(kept)[:16]}):\n{entry["latest_sample"]}'
            ))
        else:
            error_log.pop(unit, None)

    # Added 2026-09-05 after rotating a real exposed Nova key/secret -- see
    # ENV_FILES comment above. Deliberately does NOT include the matched
    # value in the alert message (would defeat the whole point by putting
    # the secret into watchdog_state.json / Telegram / the dashboard) --
    # only which line number and which pattern matched.
    for i, line in enumerate(lines):
        if _LEAKED_KEY_ID_PATTERN.search(line) or _LEAKED_SECRET_PATTERN.search(line):
            issues.append((f'leaked_credential:{unit}',
                            f'{unit} log line {i} matches an Alpaca credential shape -- a real key/secret may have '
                            f'been logged. Value redacted here; check the raw journal directly, then rotate the '
                            f'credential (see the 2026-09-05 Nova rotation for the deploy-everywhere checklist).'))
            break  # one hit is enough to alert; don't scan/report every line once found
    return issues


def check_research_agent_health(account_key, label, decisions_path):
    """Flags a symbol whose last STUCK_VETO_THRESHOLD+ research-agent
    decisions were all vetoes on the identical reasoning string -- see
    STUCK_VETO_THRESHOLD above for why this exists and what it deliberately
    doesn't claim to judge. Each bot's decisions file is {symbol: [decision,
    ...]} oldest-first (agents/state.py's record_decision / bot/
    research_agent.py's _record_decision both append), so the tail of each
    symbol's list is its most recent consecutive checks.

    Recency-gated on the last entry's own timestamp, not just position in the
    list -- a symbol a bot stops re-checking entirely (e.g. it drops off the
    watchlist, or just stops generating fresh candidate signals) would
    otherwise sit at the tail of its own history forever, alerting on
    days-old activity as if it were still happening right now. STUCK_VETO_
    MAX_AGE_HOURS mirrors research_agent.py's own LOOKBACK_HOURS=72 -- the
    same window that module already treats as still relevant."""
    issues = []
    if not os.path.exists(decisions_path):
        return issues
    try:
        with open(decisions_path) as f:
            decisions = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        issues.append((f'{account_key}:research_agent_read_error',
                        f'[{label}] Failed to read research agent decisions at {decisions_path}: {e}'))
        return issues

    now = datetime.now(timezone.utc)
    for symbol, entries in decisions.items():
        recent = entries[-STUCK_VETO_THRESHOLD:]
        if len(recent) < STUCK_VETO_THRESHOLD:
            continue
        last_ts = recent[-1].get('timestamp')
        if not last_ts:
            continue
        try:
            age_hours = (now - datetime.fromisoformat(last_ts)).total_seconds() / 3600
        except ValueError:
            continue
        if age_hours > STUCK_VETO_MAX_AGE_HOURS:
            continue  # stale history a bot stopped re-checking, not an ongoing block
        reasonings = {e.get('reasoning') for e in recent}
        if all(e.get('veto') for e in recent) and len(reasonings) == 1:
            issues.append((
                f'{account_key}:stuck_veto:{symbol}',
                f'[{label}] {symbol} has been vetoed {len(recent)} consecutive checks on the identical '
                f'reasoning ("{recent[-1].get("reasoning")}") -- worth a look: either still-fresh evidence '
                f'(fine, no action needed) or a stale/broad article that should have aged out or been filtered'
            ))
    return issues


def _service_started_at(unit):
    """When the current process of `unit` actually began, or None.

    Parses systemd's human form ("Thu 2026-09-24 11:17:57 UTC") rather than
    ActiveEnterTimestampUSec, which this systemd build does not expose at all
    -- it returns an empty string, which the first version of this function
    quietly turned into None, which check_stale_code quietly skipped. The
    check then reported zero issues while measuring nothing. A monitor whose
    failure mode is silent good news is worse than no monitor, so this returns
    None ONLY for a genuinely stopped unit.
    """
    result = subprocess.run(
        ['systemctl', 'show', '-p', 'ActiveEnterTimestamp', '--value', unit],
        capture_output=True, text=True, timeout=10)
    raw = result.stdout.strip()
    if not raw:
        return None  # unit has never run
    parts = raw.split()
    if len(parts) < 3:
        return None
    try:
        dt = datetime.strptime(f'{parts[1]} {parts[2]}', '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    # systemd renders in the machine's local zone and names it; this box is
    # UTC, but don't bake that in.
    if len(parts) > 3 and parts[3] == 'UTC':
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _newest_code_mtime(repo):
    """Newest mtime among the .py files the service actually imports, or None.

    Uses mtime, NOT the newest commit date. That distinction is the whole
    check: the question is "is the running process older than the code on
    disk", and a commit is neither necessary nor sufficient for the code on
    disk to have changed. Committing AFTER restarting -- edit, test, restart,
    then commit, which is the normal workflow here -- would make a commit-based
    version cry stale on a service that is perfectly up to date. It did
    exactly that on 2026-09-24 and briefly convinced me two bots had been
    running a day-old research agent when the files predated the restart by
    sixty seconds.

    venv/.git/__pycache__ are skipped because they are not this project's
    code; tests/ because nothing in the service process imports it.
    """
    skip = {'.git', 'venv', '__pycache__', 'node_modules', 'data', 'tests'}
    newest = None
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            if not name.endswith('.py'):
                continue
            try:
                m = os.path.getmtime(os.path.join(root, name))
            except OSError:
                continue
            if newest is None or m > newest:
                newest = m
    if newest is None:
        return None
    return datetime.fromtimestamp(newest, tz=timezone.utc)


def check_stale_code(now=None):
    """A service still running code that git has already moved past.

    The risk this covers (real, 2026-09-10): several research_agent fixes sat
    on disk for DAYS because nobody restarted the services, and every other
    check here reported healthy throughout -- service up, git clean, tests
    passing, file correct. Each was asking a different question.

    Python caches imported modules, so editing a file changes nothing until
    the process restarts. "Deployed" is therefore a claim about a PROCESS, and
    verifying it by reading the file is a category error -- the exact one made
    here, twice now (see LESSONS 21). Comparing the process start against the
    commit is the only form of the question a machine can answer.
    """
    now = now or datetime.now(timezone.utc)
    issues = []
    for unit, repo in SERVICE_REPOS.items():
        try:
            started = _service_started_at(unit)
            changed = _newest_code_mtime(repo)
        except (subprocess.SubprocessError, OSError) as e:
            issues.append((f'stale_code:{unit}:error',
                            f'{unit}: could not compare process against repo: {e}'))
            continue
        if started is None or changed is None:
            continue  # not running (check_services covers that), or not a repo
        if changed <= started:
            continue
        behind_h = (now - changed).total_seconds() / 3600
        if behind_h * 60 < STALE_CODE_GRACE_MINUTES:
            continue  # a deploy in progress, not a stuck one
        issues.append((
            f'stale_code:{unit}',
            f'{unit} is running code older than its files: process started '
            f'{started:%Y-%m-%d %H:%M}Z, newest .py under {repo} was written '
            f'{changed:%Y-%m-%d %H:%M}Z ({behind_h:.1f}h unapplied). '
            f'The file on disk is already correct -- Python caches imported '
            f'modules, so this needs `systemctl restart {unit}` to take effect.'
        ))
    return issues


def check_git_drift():
    """Uncommitted changes sitting in any fleet repo -- the one deterministic-
    checkable thing the fleet-review-agent covered that this watchdog didn't
    already (its other coverage -- service health, log errors, account state
    -- all overlap what's already checked above; its cross-file-consistency
    reasoning simply isn't replicable without an LLM, so that's genuinely
    lost, not folded in here)."""
    issues = []
    for name, path in GIT_REPOS.items():
        try:
            result = subprocess.run(['git', 'status', '--short'], cwd=path,
                                     capture_output=True, text=True, timeout=10)
        except Exception as e:
            issues.append((f'git_drift:{name}:error', f'{name}: failed to run git status: {e}'))
            continue
        if result.returncode != 0:
            issues.append((f'git_drift:{name}:error', f'{name}: git status failed: {result.stderr.strip()[:200]}'))
            continue
        dirty = result.stdout.strip()
        if dirty:
            sample = '\n'.join(dirty.splitlines()[:10])
            issues.append((f'git_drift:{name}', f'{name} ({path}) has uncommitted changes:\n{sample}'))
    return issues


def check_secrets_hygiene():
    """Two independent .env risks per fleet repo -- see ENV_FILES comment
    above for why this exists. (1) accidentally tracked by git: every commit
    on this VPS is made locally first, then relayed to GitHub from the
    user's own machine (see the "VPS has no GitHub push access" project
    notes) -- a `.env` swept into a careless `git add -A` wouldn't be caught
    until it reached GitHub, by which point it's in permanent history. (2)
    group/world-readable permissions (should always be 600, root-only) --
    confirmed all currently 600 as of 2026-09-05, this keeps it that way."""
    issues = []
    for name, path in ENV_FILES.items():
        if not os.path.exists(path):
            continue

        repo_dir = GIT_REPOS.get(name)
        if repo_dir:
            try:
                relpath = os.path.relpath(path, repo_dir)
                result = subprocess.run(['git', 'ls-files', '--error-unmatch', relpath], cwd=repo_dir,
                                         capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    issues.append((f'secrets_hygiene:{name}:tracked',
                                    f'{name}: {path} is tracked by git -- real credentials could reach GitHub on the next push'))
            except Exception as e:
                issues.append((f'secrets_hygiene:{name}:error', f'{name}: failed to check git tracking for .env: {e}'))

        try:
            mode = oct(os.stat(path).st_mode)[-3:]
            if mode != '600':
                issues.append((f'secrets_hygiene:{name}:permissions',
                                f'{name}: {path} has permissions {mode}, expected 600'))
        except Exception as e:
            issues.append((f'secrets_hygiene:{name}:error', f'{name}: failed to check .env permissions: {e}'))
    return issues


def check_account(account_key, label, api_key, secret_key, seen_order_ids):
    """Checks positions and orders independently -- a failure fetching one (e.g. a
    transient Alpaca API timeout) must not skip the other, and must not crash the
    whole script before check_services()/check_new_log_errors() even run. Each
    failure becomes its own cooldown-managed issue instead of an unhandled
    exception, so a rough API blip is reported once (like any other issue here),
    not silently swallowed by the process dying."""
    issues = []
    new_order_ids = set(seen_order_ids)

    if not api_key or not secret_key:
        issues.append((f'{account_key}:not_configured', f'[{label}] credentials not set in this watchdog\'s .env â€” skipping checks for this account'))
        return issues, new_order_ids

    client = AlpacaClient()
    client.api_key = api_key
    client.secret_key = secret_key

    try:
        for p in client.get_positions():
            pnl_pct = float(p['unrealized_plpc'])
            symbol = p['symbol']
            # Crypto's real, intentional tolerance (trader.py's own _handle_stop_loss)
            # is CRYPTO_STOP_LOSS_THRESHOLD (15%), not the stock threshold (5%) -- using
            # the flat stock threshold here false-alarmed on every crypto position
            # between 5-15% down, which is normal/expected for crypto, not a breach.
            threshold = settings.CRYPTO_STOP_LOSS_THRESHOLD if p.get('asset_class') == 'crypto' else settings.STOP_LOSS_THRESHOLD
            if pnl_pct <= -threshold:
                issues.append((f'{account_key}:stop_loss_breach:{symbol}', f'[{label}] {symbol} is down {pnl_pct * 100:.1f}% â€” past the {threshold * 100:.0f}% stop-loss threshold'))
    except Exception as e:
        issues.append((f'{account_key}:api_error:positions', f'[{label}] Failed to fetch positions from Alpaca: {e}'))

    try:
        for o in client.get_orders():
            oid = o.get('id')
            if not oid or oid in seen_order_ids:
                continue
            new_order_ids.add(oid)
            if oid in KNOWN_MANUAL_ORDER_IDS:
                continue
            source = o.get('source')
            side = o.get('side')
            symbol = o.get('symbol')
            # Alpaca's paper API returns source=None immediately after a fill, on BOTH
            # sides -- not sell-only as first thought. Originally believed this was
            # close_position() (DELETE, every sell) vs. create_order() (POST, every
            # buy) specific, since the sell case (order 6a00a285-...) was the only one
            # checked right after fill; every buy checked before 2026-08-04 just
            # happened to already be days old, past Alpaca's own backfill delay.
            # Disproven the same day: order 70f7f532-... (a genuine scan-buy-SPY-...
            # buy, confirmed legitimate by its client_order_id tag) showed source=None
            # immediately after fill too. TRANSIENT either way -- Alpaca backfills to
            # 'access_key' on its own schedule (days, per the original sell case) --
            # so treat None the same as access_key regardless of side, and only flag
            # an order as unattributed if its source is something else entirely.
            if source in (None, 'access_key'):
                issues.append((f'{account_key}:bot_order:{oid}', f'[{label}] Bot placed a {side.upper()} on {symbol} (order {oid})'))
            else:
                issues.append((f'{account_key}:unattributed_order:{oid}', f'[{label}] Order {oid} ({side} {symbol}) has source="{source}", not this account\'s own key â€” verify this wasn\'t a manual or unexpected order'))
    except Exception as e:
        issues.append((f'{account_key}:api_error:orders', f'[{label}] Failed to fetch orders from Alpaca: {e}'))

    return issues, new_order_ids


def main():
    state = load_state()
    active = state.get('active_alerts', {})
    seen_order_ids = set(state.get('seen_order_ids', []))
    last_log_check = state['last_log_check']
    now = datetime.now(timezone.utc)

    # Each check category is isolated in its own try/except so a single failing
    # check (e.g. a transient Alpaca API timeout) can't abort the whole cycle and
    # silently skip every check after it -- found 2026-09-01 after old tracebacks
    # in the log showed check_account's own exception propagating all the way up
    # through main() and killing the run before git-drift or the other accounts
    # were even checked.
    all_issues = []
    try:
        all_issues += list(check_services())
    except Exception as e:
        print(f"check_services failed: {e}")
        all_issues.append(('watchdog_internal_error:check_services', f'watchdog: check_services crashed: {e}'))

    try:
        all_issues += check_stale_code()
    except Exception as e:
        print(f"check_stale_code failed: {e}")
        all_issues.append(('watchdog_internal_error:check_stale_code', f'watchdog: check_stale_code crashed: {e}'))

    try:
        all_issues += check_git_drift()
    except Exception as e:
        print(f"check_git_drift failed: {e}")
        all_issues.append(('watchdog_internal_error:check_git_drift', f'watchdog: check_git_drift crashed: {e}'))

    try:
        all_issues += check_secrets_hygiene()
    except Exception as e:
        print(f"check_secrets_hygiene failed: {e}")
        all_issues.append(('watchdog_internal_error:check_secrets_hygiene', f'watchdog: check_secrets_hygiene crashed: {e}'))

    for account_key, cfg in ACCOUNTS.items():
        try:
            account_issues, seen_order_ids = check_account(
                account_key, cfg['label'], cfg['api_key'], cfg['secret_key'], seen_order_ids
            )
            all_issues += account_issues
        except Exception as e:
            print(f"check_account({account_key}) failed: {e}")
            all_issues.append((f'watchdog_internal_error:check_account:{account_key}', f'watchdog: check_account crashed for {cfg["label"]}: {e}'))

        try:
            all_issues += check_new_log_errors(cfg['log_unit'], last_log_check.get(cfg['log_unit']),
                                               error_log=state['log_error_log'], now=now)
        except Exception as e:
            print(f"check_new_log_errors({account_key}) failed: {e}")
            all_issues.append((f'watchdog_internal_error:check_new_log_errors:{account_key}', f'watchdog: check_new_log_errors crashed for {cfg["label"]}: {e}'))
        last_log_check[cfg['log_unit']] = now.isoformat()

        try:
            all_issues += check_research_agent_health(account_key, cfg['label'], RESEARCH_AGENT_DECISIONS_PATHS[account_key])
        except Exception as e:
            print(f"check_research_agent_health({account_key}) failed: {e}")
            all_issues.append((f'watchdog_internal_error:check_research_agent_health:{account_key}', f'watchdog: check_research_agent_health crashed for {cfg["label"]}: {e}'))

    current_keys = {key for key, _ in all_issues}

    # active_alerts[key] carries the actual message (not just a timestamp) as of
    # 2026-08-27, added so the dashboard's /api/issues can show something real --
    # 'message' is refreshed every run regardless of the Telegram cooldown below, so
    # the dashboard always reflects the latest detail even between pings; 'first_seen'
    # is preserved across cooldown cycles so the dashboard can show how long an issue
    # has been active, not just when it was last announced.
    messages = []        # urgent only -- these still reach the user's phone
    queued = []          # everything newly announced, for the agent sweep
    for key, msg in all_issues:
        existing = active.get(key) if isinstance(active.get(key), dict) else None
        first_seen = existing['first_seen'] if existing else now.isoformat()
        last_alert_at = existing.get('last_alert_at') if existing else None
        should_alert = last_alert_at is None or (
            now - datetime.fromisoformat(last_alert_at)
        ).total_seconds() > ALERT_COOLDOWN_SECONDS
        # Advisory alerts re-ping only when their message actually CHANGES.
        # A log-error alert persists for LOG_ERROR_WINDOW_HOURS rather than
        # clearing the moment errors pause, and a stuck_veto persists for
        # STUCK_VETO_MAX_AGE_HOURS; under a plain cooldown both would re-ping
        # every 2h for days about a condition nobody needs to act on again.
        # The alert stays visible in active_alerts (and on the dashboard)
        # regardless -- only the repetition stops. See is_advisory().
        if (should_alert and existing and is_advisory(key)
                and existing.get('message') == msg):
            should_alert = False
        if should_alert:
            line = msg if last_alert_at is None else f'[STILL ACTIVE] {msg}'
            # The user is not the fleet's error-reporting channel. Advisory
            # alerts go only to the agent queue; urgent ones go to both, so a
            # genuinely broken bot still reaches a human immediately even if
            # no agent session is running.
            if alert_reaches_user(key, first_seen=first_seen, now=now):
                messages.append(line)
                queued.append(('urgent', key, line))
            else:
                queued.append(('advisory', key, line))
            last_alert_at = now.isoformat()
        active[key] = {'first_seen': first_seen, 'last_alert_at': last_alert_at, 'message': msg, 'bot': bot_for_key(key)}

    for key in list(active.keys()):
        if key not in current_keys:
            del active[key]

    if messages:
        TelegramNotifier().send('AlpacaTradingBot Watchdog', '\n\n'.join(messages))
        # Every newly announced alert -- urgent or advisory -- also lands in
        # the agent review queue, which is what the sweep reads. The user is
        # not the fleet's error-reporting channel.

    queue_for_agent(queued, now=now)

    state['active_alerts'] = active
    state['seen_order_ids'] = list(seen_order_ids)
    state['last_log_check'] = last_log_check
    save_state(state)


if __name__ == '__main__':
    main()
