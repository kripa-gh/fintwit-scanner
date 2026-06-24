# scanner/telegram_channels.py
# Telegram channel registry with credibility weights.
#
# credibility : 0.0–1.0  — multiplied into conviction score downstream
# type        : used for source tagging in the report
# public      : True = no group membership required
#
# BEFORE GOING LIVE: open each channel in Telegram, read a week of posts,
# confirm it is what it claims to be. Remove any that look like pump channels.
# Channel usernames change — if a fetch returns 0 messages, the channel
# may have been renamed or deleted.

TELEGRAM_CHANNELS = {

    # ------------------------------------------------------------------ #
    # HIGH CREDIBILITY — data/regulatory feeds, minimal opinion           #
    # ------------------------------------------------------------------ #
    "NSEIndia": {
        "credibility": 1.0,
        "type": "regulatory",
        "description": "NSE India official channel",
        "public": True,
    },
    "bseindia": {
        "credibility": 1.0,
        "type": "regulatory",
        "description": "BSE India official channel",
        "public": True,
    },
    "sebiupdates": {
        "credibility": 1.0,
        "type": "regulatory",
        "description": "SEBI regulatory updates",
        "public": True,
    },
    "fiiDiidata": {
        "credibility": 0.90,
        "type": "institutional_flow",
        "description": "Daily FII/DII provisional and final flow data",
        "public": True,
    },
    "nseoptionchain": {
        "credibility": 0.85,
        "type": "options_flow",
        "description": "NSE options chain data — PCR, OI changes, max pain",
        "public": True,
    },
    "cnbctv18news": {
        "credibility": 0.80,
        "type": "news",
        "description": "CNBC TV18 market news",
        "public": True,
    },
    "economictimes": {
        "credibility": 0.80,
        "type": "news",
        "description": "Economic Times markets feed",
        "public": True,
    },

    # ------------------------------------------------------------------ #
    # MEDIUM CREDIBILITY — established TA / institutional commentary      #
    # ------------------------------------------------------------------ #
    "nifty_technicals": {
        "credibility": 0.65,
        "type": "technical",
        "description": "Nifty and Bank Nifty technical levels",
        "public": True,
    },
    "stockbreakoutalerts": {
        "credibility": 0.60,
        "type": "technical",
        "description": "Breakout scanner alerts — volume + price action",
        "public": True,
    },
    "bulkblockdeals": {
        "credibility": 0.75,
        "type": "institutional_flow",
        "description": "BSE/NSE bulk and block deal aggregator",
        "public": True,
    },
    "quarterly_results_india": {
        "credibility": 0.75,
        "type": "fundamental",
        "description": "Quarterly results summaries — beat/miss/guidance",
        "public": True,
    },

    # ------------------------------------------------------------------ #
    # LOWER CREDIBILITY — retail/social flow, high noise                  #
    # Keep threshold at 0.45 so these still make it through,             #
    # but their conviction scores are discounted automatically.           #
    # ------------------------------------------------------------------ #
    "dalal_street_talks": {
        "credibility": 0.45,
        "type": "social",
        "description": "Retail market discussion — Dalal Street community",
        "public": True,
    },
}

# ----------------------------------------------------------------------- #
# Pipeline thresholds — tune after first week of runs                      #
# ----------------------------------------------------------------------- #

# Messages from channels below this credibility are fetched but discarded
# before being sent to Claude. Saves tokens on pure noise.
CREDIBILITY_THRESHOLD = 0.45

# Channel types that bypass the signal threshold check in main.py
# (regulatory and institutional data always passes through regardless
# of mention count or sentiment score)
HIGH_TRUST_TYPES = {"regulatory", "institutional_flow", "news", "fundamental"}

# Per-channel message cap — prevents a single high-volume channel
# from flooding the pipeline and dominating Claude's context
MAX_MESSAGES_PER_CHANNEL = 50
