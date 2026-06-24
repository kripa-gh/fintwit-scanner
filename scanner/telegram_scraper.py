# scanner/telegram_scraper.py
#
# Fetches messages from curated Indian market Telegram channels.
# Uses Telethon StringSession — no .session file, no base64 gymnastics.
# All auth lives in TELEGRAM_SESSION_STRING GitHub Secret.
#
# Entry point for main.py:
#   from telegram_scraper import fetch_telegram_messages, telegram_available

import os
import asyncio
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.sessions import StringSession

from scanner.telegram_channels import (
    TELEGRAM_CHANNELS,
    CREDIBILITY_THRESHOLD,
    HIGH_TRUST_TYPES,
    MAX_MESSAGES_PER_CHANNEL,
)

IST = timezone(timedelta(hours=5, minutes=30))


class TelegramScraper:
    """Scrapes configured Telegram channels and returns normalised message dicts."""

    def __init__(self):
        api_id = os.environ.get("TELEGRAM_API_ID")
        api_hash = os.environ.get("TELEGRAM_API_HASH")
        session_string = os.environ.get("TELEGRAM_SESSION_STRING")

        missing = [
            name for name, val in {
                "TELEGRAM_API_ID": api_id,
                "TELEGRAM_API_HASH": api_hash,
                "TELEGRAM_SESSION_STRING": session_string,
            }.items()
            if not val
        ]
        if missing:
            raise EnvironmentError(
                f"[Telegram] Missing secrets: {', '.join(missing)}"
            )

        self.client = TelegramClient(
            StringSession(session_string),
            int(api_id),
            api_hash,
        )

        # Only include channels that meet the credibility threshold
        self.active_channels = {
            username: meta
            for username, meta in TELEGRAM_CHANNELS.items()
            if meta["credibility"] >= CREDIBILITY_THRESHOLD
        }
        print(
            f"[Telegram] {len(self.active_channels)} channels active "
            f"(threshold ≥ {CREDIBILITY_THRESHOLD})"
        )

    async def fetch_all(self, lookback_hours: int = 48) -> list[dict]:
        """Fetch and merge messages from all active channels."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        all_messages = []

        async with self.client:
            for username, meta in self.active_channels.items():
                try:
                    msgs = await self._fetch_channel(username, meta, cutoff)
                    all_messages.extend(msgs)
                    print(
                        f"[Telegram] @{username} "
                        f"({meta['type']}, cred={meta['credibility']}): "
                        f"{len(msgs)} messages"
                    )
                except Exception as exc:
                    # Never let one bad channel kill the whole scrape
                    print(f"[Telegram] @{username}: FAILED — {exc}")
                    continue

        # Deduplicate by channel + message_id
        seen: set[str] = set()
        unique: list[dict] = []
        for msg in all_messages:
            key = f"{msg['channel']}:{msg['message_id']}"
            if key not in seen:
                seen.add(key)
                unique.append(msg)

        dupes = len(all_messages) - len(unique)
        print(
            f"[Telegram] Total: {len(unique)} messages "
            f"({dupes} dupes removed) across "
            f"{len(self.active_channels)} channels"
        )
        return unique

    async def _fetch_channel(
        self,
        username: str,
        meta: dict,
        cutoff: datetime,
    ) -> list[dict]:
        """Fetch messages from a single channel newer than cutoff."""
        messages: list[dict] = []

        async for msg in self.client.iter_messages(
            username, limit=MAX_MESSAGES_PER_CHANNEL
        ):
            # iter_messages returns newest → oldest.
            # Use continue (not break) — Telegram channels occasionally
            # pin old messages that appear out of chronological order,
            # same class of bug as the Twitter break → continue fix.
            if not msg.date:
                continue

            msg_time = msg.date.replace(tzinfo=timezone.utc)
            if msg_time < cutoff:
                continue

            text = (msg.text or "").strip()
            if len(text) < 10:
                # Skip empty messages, stickers, media-only posts
                continue

            messages.append({
                # --- Source metadata ---
                "source": "telegram",
                "channel": username,
                "channel_type": meta["type"],
                "channel_credibility": meta["credibility"],
                "is_high_trust": meta["type"] in HIGH_TRUST_TYPES,

                # --- Message fields ---
                "message_id": msg.id,
                "text": text,
                "date": msg_time.isoformat(),

                # --- Engagement signals ---
                # Channels show view counts; groups don't
                "views": getattr(msg, "views", 0) or 0,
                "forwards": getattr(msg, "forwards", 0) or 0,

                # --- Twitter compat fields (set to None so downstream
                #     code that checks these doesn't KeyError) ---
                "username": None,
                "display_name": username,
                "followers": None,
                "tweet_id": None,
            })

        return messages


# --------------------------------------------------------------------------- #
# Public API — import these in main.py                                         #
# --------------------------------------------------------------------------- #

def fetch_telegram_messages(lookback_hours: int = 48) -> list[dict]:
    """
    Synchronous entry point for the main pipeline.

    Usage in main.py:
        from telegram_scraper import fetch_telegram_messages, telegram_available

        if telegram_available():
            telegram_msgs = fetch_telegram_messages(lookback_hours=48)
    """
    scraper = TelegramScraper()
    return asyncio.run(scraper.fetch_all(lookback_hours=lookback_hours))


def telegram_available() -> bool:
    """
    Returns True if all three Telegram secrets are present.
    Use this to gate the Telegram scrape in main.py so the pipeline
    degrades gracefully when secrets aren't configured.
    """
    return all([
        os.environ.get("TELEGRAM_API_ID"),
        os.environ.get("TELEGRAM_API_HASH"),
        os.environ.get("TELEGRAM_SESSION_STRING"),
    ])


def normalise_for_pipeline(telegram_messages: list[dict]) -> list[dict]:
    """
    Convert raw Telegram message dicts to the same schema as Twitter tweet
    dicts so tweet_intelligence.py and ticker_extractor.py consume both
    sources without any modification.

    Key mappings:
      message_id  → id          (prefixed tg_ to avoid collision with tweet IDs)
      channel     → username    (used for credibility lookup key)
      forwards    → retweet_amplification
      hashtags/cashtags defaulted to [] — _extract_candidates() regex
                                          handles #TICKER and $TICKER in text

    Telegram-specific fields (source, channel_credibility, etc.) are
    preserved so the credibility weighting block in main.py can use them.
    """
    normalised = []
    for msg in telegram_messages:
        normalised.append({
            # ── Fields tweet_intelligence.py reads ──────────────────────────
            "id":       f"tg_{msg['message_id']}",   # unique across both sources
            "username": msg["channel"],               # channel name as author
            "text":     msg["text"],

            # ── Fields ticker_extractor.py reads ────────────────────────────
            "date":                  msg.get("date", ""),
            "url":                   "",
            "hashtags":              [],   # regex in _extract_candidates handles raw text
            "cashtags":              [],   # regex in _extract_candidates handles raw text
            "retweet_amplification": int(msg.get("forwards", 0)),
            "retweeters":            [],

            # ── Telegram metadata — preserved for weighting ─────────────────
            "source":              "telegram",
            "channel":             msg["channel"],
            "channel_type":        msg.get("channel_type", "unknown"),
            "channel_credibility": msg.get("channel_credibility", 0.5),
            "is_high_trust":       msg.get("is_high_trust", False),
        })
    return normalised
