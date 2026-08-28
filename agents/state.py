"""Persistence for research_agent.py's decisions -- deliberately separate
from trader.py's six state files (peak_prices_state.json,
position_opened_state.json, reentry_state.json, position_method_state.json,
trade_history.json, zero_since_state.json). Hard rule: nothing in agents/
ever reads or writes those six files, in either direction -- see the plan
at C:\\Users\\kevca\\.claude\\plans\\harmonic-crafting-donut.md for why (this
is what lets the Phase 1 wiring be a plain synchronous in-process call with
no locking, instead of two processes racing for the same files).
"""
import json
import logging
import os
from datetime import datetime
from typing import Dict

import pytz

from config import settings

logger = logging.getLogger(__name__)

AGENT_DECISIONS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'agent_decisions_state.json',
)


def load_agent_decisions() -> Dict:
    """Same load idiom as trader.py's other state files: missing/corrupt
    file is treated as empty, never raises."""
    if os.path.exists(AGENT_DECISIONS_FILE):
        try:
            with open(AGENT_DECISIONS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load agent decisions state, starting fresh: {e}")
    return {}


def save_agent_decisions(decisions: Dict):
    try:
        with open(AGENT_DECISIONS_FILE, 'w') as f:
            json.dump(decisions, f, indent=2)
    except OSError as e:
        logger.error(f"Failed to save agent decisions state: {e}")


def record_decision(symbol: str, decision: Dict):
    """Append one decision to this symbol's log (most recent last). Called
    after every propose() call, whether Phase 0 shadow or Phase 1 wired-in --
    this is purely an observation/audit trail, never read back by the agent
    itself to inform a future decision.

    Stamps 'timestamp' here (not inside research_agent.py's decision dict
    itself) so every caller gets one automatically -- added when the
    dashboard extension needed a way to sort/display "when," matching
    trader.py: _now()'s exact convention (REPORT_TIMEZONE, ISO format) for
    consistency with every other timestamp already logged across this
    project. Backward compatible: entries recorded before this existed
    simply have no 'timestamp' key, and any reader must treat that as
    "unknown / sorts last," not assume the key is always present.
    """
    decision = dict(decision, timestamp=datetime.now(pytz.timezone(settings.REPORT_TIMEZONE)).isoformat())
    decisions = load_agent_decisions()
    decisions.setdefault(symbol, []).append(decision)
    save_agent_decisions(decisions)
