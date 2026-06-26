"""
scanner/promo_filter.py

Detects backward-looking promotional / distribution-phase chatter in trader
messages (Twitter + Telegram) so it is NOT counted as a fresh bullish signal.

WHY THIS EXISTS
---------------
The sentiment engine was scoring messages like
    "Recommended at 1438, now 3147, 100% returns for premium members"
as Strong Bullish. That is a realized-gain brag — a LATE / distribution tell,
not an entry signal. This module flags that class of message.

CRITICAL DESIGN RULE
--------------------
A *forward* trade idea is legitimate signal and MUST pass through untouched:
    "buy X, target 500, SL 450"      -> keep
    "added IOCL for long term"        -> keep
    "initiated at 2039, SL 2012"      -> keep
The ONLY things to catch are:
    1. realized-gain bragging   ("rallied 98% from 343 to 680", "100% returns")
    2. paywall solicitation     ("join premium", "limited seats", "DM for link")

This module ONLY classifies. Score/aggregate changes are wired in by the caller
(see INTEGRATION at the bottom). It has zero third-party dependencies.
"""

from __future__ import annotations
import re
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Pattern banks
# --------------------------------------------------------------------------

# 1) Backward-looking realized-gain bragging.
_GAIN_BRAG = [
    r"\b\d{2,4}\s?%\s?(?:returns?|gains?|profit|up|rally|rallied)\b",          # "100% returns"
    r"\b(?:rallied|jumped|surged|gained|ran|up)\s?(?:~|about|nearly)?\s?\d{2,4}\s?%",  # "rallied ~98%"
    r"\bfrom\s?₹?\s?[\d,]+(?:\.\d+)?\s+to\s?₹?\s?[\d,]+",                       # "from 343.50 to 680"  (FP knob: see notes)
    r"\brecommend(?:ed|ation)?\b.*?\b(?:now|to|rallied|hit|delivered?|giving)\b.*?[\d,]",  # "recommended at 1438 ... rallied to 3147"
    r"\b(?:multi[\s-]?bagger|\d+\s?bagger)\b",                                 # "multibagger" / "5 bagger"
    r"\bbooked\s?(?:profit|gains?|\d)\b",                                      # "booked profit"
    r"\b(?:told you|called it|as predicted|as i said|as we said)\b",           # pure brag phrases
]

# 2) Paywall / subscription solicitation.
_SOLICIT = [
    r"\bpremium\b.{0,40}?\b(?:member|channel|group|call|join|subscrib)",
    r"\bmembers?\b.{0,30}?\b(?:made|earned|profit|gain|return)",
    r"\b(?:join|subscrib\w*|dm|whats?app)\b.{0,30}?\b(?:premium|paid|link|channel|group)\b",
    r"\blimited\s?(?:seats|slots|time)\b",
    r"\bfor\s?(?:our\s?)?(?:premium|paid)\s?members\b",
]

_GAIN_RE    = [re.compile(p, re.I) for p in _GAIN_BRAG]
_SOLICIT_RE = [re.compile(p, re.I) for p in _SOLICIT]


@dataclass
class PromoVerdict:
    is_promo: bool
    kind: str       # "realized_gain_brag" | "solicitation" | "none"
    reason: str     # the matched fragment (for logging / report tagging)


def classify_message(text: str) -> PromoVerdict:
    """Classify a single message's raw text."""
    if not text:
        return PromoVerdict(False, "none", "")

    # Gain brag wins even when forward-idea words are also present:
    # a message bragging about realized gains is late regardless of any
    # "buy now" tacked on the end.
    for rx in _GAIN_RE:
        m = rx.search(text)
        if m:
            return PromoVerdict(True, "realized_gain_brag", m.group(0).strip())

    for rx in _SOLICIT_RE:
        m = rx.search(text)
        if m:
            return PromoVerdict(True, "solicitation", m.group(0).strip())

    return PromoVerdict(False, "none", "")


def annotate_messages(messages, get_text=None):
    """
    Tag each message dict IN PLACE:
        m["promo"]        -> bool
        m["promo_kind"]   -> str
        m["promo_reason"] -> str
    Returns (n_promo, n_total).

    get_text: optional callable(message)->str. If None, tries common keys.
    """
    def _default_text(m):
        if isinstance(m, dict):
            for k in ("text", "content", "rawContent", "message", "body", "summary"):
                v = m.get(k)
                if v:
                    return str(v)
        return str(m) if m else ""

    gt = get_text or _default_text
    n_promo = 0
    for m in messages:
        v = classify_message(gt(m))
        if isinstance(m, dict):
            m["promo"] = v.is_promo
            m["promo_kind"] = v.kind
            m["promo_reason"] = v.reason
        n_promo += int(v.is_promo)
    return n_promo, len(messages)


# --------------------------------------------------------------------------
# INTEGRATION (wire this in tweet_intelligence.py — needs your actual code)
# --------------------------------------------------------------------------
#
# OPTION 1 — preferred, inside analyse_all_tweets() per-message tally:
#   from scanner.promo_filter import classify_message
#   v = classify_message(msg_text)
#   if v.is_promo:
#       msg["promo_tag"] = f"[{v.kind}]"     # so the report bullet can show it
#       continue                             # <-- do NOT add to bullish tally /
#                                            #     do NOT increment mention count
#   # ...existing bullish/bearish scoring for non-promo messages...
#
# OPTION 2 — decoupled, no prompt change, run after analyse_all_tweets():
#   from scanner.promo_filter import annotate_messages
#   annotate_messages(all_messages)          # tags each message
#   # then, when computing tweet_signals[ticker]["sentiment_score"] and the
#   # mention count, exclude messages where m["promo"] is True.
#
# DISPLAY: keep promo messages visible in the report but tagged, e.g.
#   "→ [LATE/PROMO] Recommended at 1438, now 3147 ..."  (greyed, score-excluded)
#   Transparency beats silently dropping them.

if __name__ == "__main__":
    # Self-test against the ACTUAL strings from your 25 Jun report + the
    # legitimate forward ideas that must survive.
    cases = [
        # (text, expected_is_promo)
        ("Recommended at 1438, stock has rallied to 3147, delivering over 100% returns for premium channel members", True),
        ("Hidden gem stock rallied ~98% from 343.50 to 680 in about 2.5 months after premium channel recommendation", True),
        ("Join premium now, limited seats, DM for the link", True),
        ("Multibagger alert, booked profit at the top", True),
        ("Small position initiated at 2039 with SL at 2012, targeting 2200 level", False),
        ("Bullish on Relaxo if market moves up, with 3.5% stop loss defined", False),
        ("Oil marketing companies expected to benefit; added IOCL for medium to long term", False),
        ("Above 2063 expected big move upside for Wockhardt", False),
    ]
    ok = 0
    for text, exp in cases:
        v = classify_message(text)
        flag = "PROMO" if v.is_promo else "clean"
        mark = "OK " if v.is_promo == exp else "FAIL"
        print(f"[{flag:5}] {mark} kind={v.kind:18} :: {text[:64]}")
        ok += int(v.is_promo == exp)
    print(f"\n{ok}/{len(cases)} correct")
