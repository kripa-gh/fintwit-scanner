# scanner/telegram_channels.py
# Telegram channel registry with credibility weights.
#
# All channels now use permanent numeric IDs instead of usernames.
# Numeric IDs never change even if the channel is renamed.
#
# credibility     : 0.0–1.0 — multiplied into conviction score downstream
# type            : used for source tagging in the report
# public          : True = no group membership required
# whitelist_users : optional list of Telegram user IDs — if set, only messages
#                   from those users are kept. Leave empty [] to include everyone.
#                   To get a user's ID: forward their message to @userinfobot.

TELEGRAM_CHANNELS = {

    # ------------------------------------------------------------------ #
    # PUBLIC CHANNELS — permanent numeric IDs                             #
    # ------------------------------------------------------------------ #
    "-1001306340643": {
        "credibility": 0.60,
        "type": "technical",
        "description": "Momentum Trades — breakout and swing setups",
        "public": True,
        "whitelist_users": [],
    },
    "-1002347392788": {
        "credibility": 0.60,
        "type": "technical",
        "description": "Ruthless Trader — technical analysis and calls",
        "public": True,
        "whitelist_users": [],
    },
    "-1002470006457": {
        "credibility": 0.60,
        "type": "technical",
        "description": "STR / Sunrice Trading Room — swing and positional trades",
        "public": True,
        "whitelist_users": [],
    },
    "-1001671811240": {
        "credibility": 0.65,
        "type": "technical",
        "description": "Buy Before Breakout — pre-breakout stock alerts",
        "public": True,
        "whitelist_users": [],
    },
    "-1001642892147": {
        "credibility": 0.60,
        "type": "technical",
        "description": "Breakout Charts — chart pattern alerts",
        "public": True,
        "whitelist_users": [],
    },
    "-1001739800283": {
        "credibility": 0.60,
        "type": "technical",
        "description": "UremO — Indian market trade ideas",
        "public": True,
        "whitelist_users": [],
    },

    # ------------------------------------------------------------------ #
    # PRIVATE CHANNELS                                                     #
    # ------------------------------------------------------------------ #
    "-1001729217563": {
        "credibility": 0.70,
        "type": "technical",
        "description": "UremO Premium — private channel, all members",
        "public": False,
        "whitelist_users": [],   # empty = include all members
    },
    "-1001427374929": {
        "credibility": 0.75,
        "type": "technical",
        "description": "VIES-Family — Dinesh Agrawal picks only",
        "public": False,
        "whitelist_users": [
            463054735,   # Dinesh Agrawal
            # Add more trusted member IDs here via @userinfobot
        ],
    },
}

# ----------------------------------------------------------------------- #
# Pipeline thresholds                                                       #
# ----------------------------------------------------------------------- #

CREDIBILITY_THRESHOLD = 0.45
HIGH_TRUST_TYPES = {"regulatory", "institutional_flow", "news", "fundamental"}
MAX_MESSAGES_PER_CHANNEL = 50
