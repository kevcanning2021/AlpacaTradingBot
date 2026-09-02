"""Free, zero-cost news-based veto check -- replaced the Claude + web-search
version 2026-09-02 after the user found the real dollar cost added up too
fast for what it was providing ($12 burned quickly for a "double-check
before buying" feature). Fetches recent headlines via Alpaca's own News API
(same credentials already used for market data/trading -- no separate
signup, no per-call cost) and scans them for a fixed list of red-flag
keywords. Same fail-open philosophy and the exact same propose() return
contract as the version this replaces, so trader.py's call sites and the
dashboard's decision-history reader don't need to change.

Real tradeoff, stated plainly: this catches obvious, explicitly-worded red
flags ("lawsuit", "bankruptcy", "recall", ...) in a headline, not the
nuanced judgment a real LLM read would have given. It will miss subtler news
that doesn't use one of these exact words, and it says nothing about news
that's actually positive-but-irrelevant. Free forever beats
occasionally-smarter at a real dollar cost -- the explicit call the user
made 2026-09-02.
"""
import logging
from typing import Dict, List, Optional

from agents.state import record_decision

logger = logging.getLogger(__name__)

# Deliberately a plain keyword list, not a sentiment model -- easy to read,
# easy to audit, easy to extend by hand. Case-insensitive substring match
# against headline + summary combined.
RED_FLAG_KEYWORDS = [
    'lawsuit', 'sues', 'sued', 'litigation',
    'investigation', 'investigated', 'probe', 'subpoena',
    'fraud', 'fraudulent', 'sec charges', 'indictment', 'indicted',
    'bankruptcy', 'bankrupt', 'chapter 11', 'insolvent', 'insolvency',
    'recall', 'recalls', 'recalled',
    'downgrade', 'downgraded', 'downgrades',
    'delisted', 'delisting', 'trading halt', 'halted',
    'restatement', 'restated', 'accounting error',
    'default', 'defaults', 'covenant breach',
    'layoffs', 'layoff', 'mass layoffs',
    'resigns', 'resignation', 'steps down', 'ousted',
    'plunge', 'plunges', 'plummet', 'plummets', 'crash', 'crashes',
    'misses estimates', 'guidance cut', 'cuts guidance', 'warns',
]

# News older than this isn't treated as a fresh reason to veto an entry -- a
# lawsuit from 3 weeks ago is already priced in; one from 6 hours ago might
# not be yet. 72h comfortably covers a weekend gap (Friday close -> Monday
# scan) without reaching back into genuinely stale news.
LOOKBACK_HOURS = 72
NEWS_LIMIT = 15


def _fail_open(symbol: str, reason: str) -> Dict:
    decision = {'veto': False, 'confidence': None, 'reasoning': f'agent call failed: {reason}', 'risk_flags': [], 'failed': True}
    record_decision(symbol, decision)
    logger.warning(f"[research_agent] {symbol}: failed open ({reason})")
    return decision


def propose(signal: Dict, recent_bars: Optional[List[Dict]] = None, *, client=None) -> Dict:
    """signal: one entry from scanner.py: OpportunityScanner.scan()'s output
    (must have 'symbol'). recent_bars is accepted for interface compatibility
    with the version this replaces but unused -- the free version has
    nothing to do with recent price action, only recent news. client: an
    AlpacaClient instance, used to fetch news (keyword-only, required in
    practice -- a missing client fails open loudly rather than silently
    skipping the check).

    Returns {'veto': bool, 'confidence': float|None, 'reasoning': str,
    'risk_flags': list[str], 'failed': bool} -- same shape as the version
    this replaces, so callers and the dashboard's decision-history reader
    don't need to change. confidence is always None here: a keyword match
    isn't a probability, and reporting a fake precision number would be
    worse than admitting this method doesn't have one.
    """
    symbol = signal['symbol']
    if client is None:
        return _fail_open(symbol, 'no Alpaca client provided')

    try:
        articles = client.get_news(symbol, lookback_hours=LOOKBACK_HOURS, limit=NEWS_LIMIT)
    except Exception as e:
        return _fail_open(symbol, f'news fetch failed: {type(e).__name__}: {e}')

    matched = []
    for article in articles:
        text = f"{article.get('headline', '')} {article.get('summary', '')}".lower()
        for keyword in RED_FLAG_KEYWORDS:
            if keyword in text:
                matched.append((keyword, article.get('headline', '')))
                break  # one match is enough to flag this article

    if matched:
        risk_flags = sorted({kw for kw, _ in matched})
        headline_examples = '; '.join(f'"{h}"' for _, h in matched[:3])
        decision = {
            'veto': True,
            'confidence': None,
            'reasoning': f'Found {len(matched)} recent article(s) with red-flag keywords {risk_flags}: {headline_examples}',
            'risk_flags': risk_flags,
            'failed': False,
        }
    else:
        decision = {
            'veto': False,
            'confidence': None,
            'reasoning': f'No red-flag keywords found in {len(articles)} recent article(s) (last {LOOKBACK_HOURS}h)',
            'risk_flags': [],
            'failed': False,
        }

    record_decision(symbol, decision)
    logger.info(f"[research_agent] {symbol}: veto={decision['veto']} ({len(articles)} articles checked)")
    return decision
