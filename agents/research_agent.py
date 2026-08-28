"""Phase 0 shadow research agent -- reads a scanner signal (+ optionally
recent bars for context), may search the web for concrete recent news, and
returns a structured veto/confidence judgment. Zero coupling to the live
order path as of Phase 0: nothing in trader.py/scheduler.py calls this yet
(config/settings.py: RESEARCH_AGENT_ENABLED / RESEARCH_AGENT_VETO_ENABLED,
both default False). See the plan at
C:\\Users\\kevca\\.claude\\plans\\harmonic-crafting-donut.md for the full
phased rollout this is Phase 0 of.

Design notes, from live verification against the real API before writing
this (2026-08-25) -- not guessed from docs alone:
- Uses a single client.messages.create() call with the server-side
  web_search tool. Server tools resolve within the same call (Anthropic's
  infra runs the search, no client-side execution loop needed) -- confirmed
  live, not just via docs.
- output_config's raw JSON schema composes with tools in the same call --
  also confirmed live. The final decision text lands as the LAST text block
  in response.content, after any thinking/server_tool_use/
  web_search_tool_result/code_execution_tool_result blocks (web_search
  bundles code execution under the hood; this is expected, not an error
  condition to guard against).
- output_config.format.schema's "number" type does NOT support
  minimum/maximum constraints (confirmed via a real 400) -- range is
  documented in the field description instead and enforced by nothing
  but the prompt; downstream code must not assume confidence is always
  in [0, 1].
- allowed_domains rejects any domain that blocks Anthropic's crawler
  (reuters.com and marketwatch.com both do, confirmed via real 400s) --
  DEFAULT_ALLOWED_DOMAINS below is only what's been verified reachable.
  Get_bars/get_bars_multi are deliberately NOT wired up as model-invoked
  tools here -- the caller pre-fetches bars via the same alpaca_client.py
  scanner.py already uses and passes a short summary as plain text context,
  avoiding a custom tool-execution loop for what's currently a read-only,
  single-symbol decision.
"""
import json
import logging
import os
from typing import Dict, List, Optional

import anthropic

from config import settings
from agents.state import record_decision

logger = logging.getLogger(__name__)

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "veto": {"type": "boolean"},
        "confidence": {"type": "number", "description": "0.0 (no real read) to 1.0 (high confidence)"},
        "reasoning": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["veto", "confidence", "reasoning", "risk_flags"],
    "additionalProperties": False,
}

# Only domains confirmed reachable by Anthropic's web_search crawler (2026-08-25
# live test) -- reuters.com and marketwatch.com both rejected the request
# outright with a 400 before this list was narrowed. Don't add a domain here
# without checking it actually works; a bad one 400s the whole call (caught
# below and treated as a failed call, not silently dropped from the list).
DEFAULT_ALLOWED_DOMAINS = ["cnbc.com", "finance.yahoo.com"]

SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prompts', 'research_system.md')


def _load_system_prompt() -> str:
    with open(SYSTEM_PROMPT_PATH) as f:
        return f.read()


def _usage_dict(response) -> Dict:
    return {'input_tokens': response.usage.input_tokens, 'output_tokens': response.usage.output_tokens}


def _fail_open(symbol: str, reason: str, usage: Optional[Dict] = None) -> Dict:
    """Any failure here means 'no opinion' -- the deterministic scanner
    signal this was consuming is left exactly as-is. Per the approved plan:
    fail toward the already-validated deterministic path, never toward
    blocking a trade or trusting a broken/partial response. Callers must
    treat failed=True identically to veto=False, not as a reason to skip or
    retry inline. usage (input_tokens/output_tokens) is included whenever a
    response actually came back -- a refusal or non-terminal stop still
    bills real tokens, only a call that never completed at all has none."""
    decision = {'veto': False, 'confidence': None, 'reasoning': f'agent call failed: {reason}', 'risk_flags': [], 'failed': True}
    if usage is not None:
        decision['usage'] = usage
    record_decision(symbol, decision)
    logger.warning(f"[research_agent] {symbol}: failed open ({reason})")
    return decision


def propose(signal: Dict, recent_bars: Optional[List[Dict]] = None) -> Dict:
    """signal: one entry from scanner.py: OpportunityScanner.scan()'s output
    (must have 'symbol', 'signal', 'reason', 'price'; 'rsi' if present is
    included as context). recent_bars: optional pre-fetched OHLCV bars, same
    shape as alpaca_client.get_bars()'s return -- caller's responsibility to
    fetch, this function makes no Alpaca API calls itself.

    Returns {'veto': bool, 'confidence': float|None, 'reasoning': str,
    'risk_flags': list[str], 'failed': bool}. Every call, success or
    failure, is recorded via agents.state.record_decision() as an audit
    trail -- Phase 0 has nothing reading this back to inform a future
    decision.
    """
    if not settings.ANTHROPIC_API_KEY:
        return _fail_open(signal['symbol'], 'no ANTHROPIC_API_KEY configured')

    bars_summary = ''
    if recent_bars:
        last = recent_bars[-5:]
        bars_summary = '\n\nLast {} bars (oldest first, close prices only): {}'.format(
            len(last), json.dumps([b.get('c') for b in last])
        )

    user_content = (
        f"Scanner signal for {signal['symbol']}: {signal['signal']} "
        f"(reason: {signal.get('reason', 'n/a')}, price: {signal.get('price')}, "
        f"rsi: {signal.get('rsi', 'n/a')})." + bars_summary
    )

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.RESEARCH_AGENT_MODEL,
            max_tokens=1024,
            system=_load_system_prompt(),
            tools=[{
                'type': 'web_search_20260209',
                'name': 'web_search',
                'max_uses': 3,
                'allowed_domains': DEFAULT_ALLOWED_DOMAINS,
            }],
            output_config={'format': {'type': 'json_schema', 'schema': DECISION_SCHEMA}},
            messages=[{'role': 'user', 'content': user_content}],
        )
    except anthropic.APIError as e:
        return _fail_open(signal['symbol'], f'{type(e).__name__}: {e}')

    usage = _usage_dict(response)

    if response.stop_reason == 'refusal':
        category = getattr(response.stop_details, 'category', 'unknown') if response.stop_details else 'unknown'
        return _fail_open(signal['symbol'], f'model refused: {category}', usage=usage)

    if response.stop_reason != 'end_turn':
        # Covers pause_turn (a long-running server-tool turn that would need
        # a resume loop -- not implemented for this shadow-only phase) and
        # any other non-terminal stop_reason. Failing open here rather than
        # guessing at a resume is the same "fail toward the deterministic
        # path" choice as every other branch in this function.
        return _fail_open(signal['symbol'], f'non-terminal stop_reason: {response.stop_reason}', usage=usage)

    text_blocks = [b.text for b in response.content if b.type == 'text']
    if not text_blocks:
        return _fail_open(signal['symbol'], 'no text block in response', usage=usage)

    try:
        decision = json.loads(text_blocks[-1])
    except json.JSONDecodeError as e:
        return _fail_open(signal['symbol'], f'malformed JSON: {e}', usage=usage)

    decision['failed'] = False
    decision['usage'] = usage
    record_decision(signal['symbol'], decision)
    logger.info(f"[research_agent] {signal['symbol']}: veto={decision.get('veto')} confidence={decision.get('confidence')} "
                f"input_tokens={usage['input_tokens']} output_tokens={usage['output_tokens']}")
    return decision
