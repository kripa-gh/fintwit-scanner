"""
twitter_scraper.py — Gap 4 Fix (retweet deduplication)
Fetches list members and recent tweets.

Key fix: retweets are deduplicated — if 20 accounts RT the same post,
it counts as 1 mention from the original author, not 20.
The retweet count is tracked separately as an amplification signal.
"""

import asyncio
import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from twscrape import API, gather

logger = logging.getLogger(__name__)

TWITTER_LIST_ID      = os.getenv("TWITTER_LIST_ID", "1506463545642217474")
AUTH_TOKEN           = os.getenv("TWITTER_AUTH_TOKEN", "")
CT0_TOKEN            = os.getenv("TWITTER_CT0", "")
TWEET_LOOKBACK_HOURS = int(os.getenv("TWEET_LOOKBACK_HOURS", "24"))
MAX_TWEETS           = int(os.getenv("MAX_TWEETS", "1000"))


async def _setup_api() -> API:
    if not AUTH_TOKEN or not CT0_TOKEN:
        raise ValueError("TWITTER_AUTH_TOKEN and TWITTER_CT0 must be set.")
    api = API()
    await api.pool.add_account_cookies("fintwit_account", f"auth_token={AUTH_TOKEN}; ct0={CT0_TOKEN}")
    return api


async def _fetch_members(api: API) -> List[Dict]:
    members = []
    async for user in api.list_members(int(TWITTER_LIST_ID)):
        members.append({
            "username":    user.username,
            "displayname": user.displayname,
            "followers":   user.followersCount,
        })
    logger.info(f"Fetched {len(members)} list members")
    return members


async def _fetch_tweets(api: API) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Fetch tweets and deduplicate retweets.

    Returns:
      - deduplicated_tweets: list of unique original tweets
      - retweet_counts: {tweet_id: number_of_times_retweeted_within_list}

    Deduplication logic:
      - If tweet has retweetedTweet field, record the original tweet ID
      - Count how many list members retweeted each original
      - Original tweet appears once with retweet_count = amplification score
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TWEET_LOOKBACK_HOURS)

    raw_tweets     = []
    retweet_map    = {}  # original_id -> {tweet_dict, retweeters: set}
    original_ids   = set()

    async for tweet in api.list_timeline(int(TWITTER_LIST_ID), limit=MAX_TWEETS):
        if tweet.date < cutoff:
            break

        # Check if this is a retweet
        rt = tweet.retweetedTweet
        if rt is not None:
            # This is a retweet — track amplification, don't add as new tweet
            orig_id = str(rt.id)
            if orig_id not in retweet_map:
                retweet_map[orig_id] = {
                    "id":         orig_id,
                    "username":   rt.user.username if rt.user else "unknown",
                    "date":       rt.date.isoformat(),
                    "text":       rt.rawContent or "",
                    "hashtags":   rt.hashtags or [],
                    "cashtags":   rt.cashtags or [],
                    "likes":      rt.likeCount,
                    "retweets":   rt.retweetCount,
                    "url":        rt.url or f"https://x.com/i/status/{orig_id}",
                    "retweeters": set(),
                    "is_retweet": False,
                    "retweet_amplification": 0,
                }
            retweet_map[orig_id]["retweeters"].add(tweet.user.username)
            retweet_map[orig_id]["retweet_amplification"] += 1
            original_ids.add(orig_id)
        else:
            # Original tweet
            raw_tweets.append({
                "id":         str(tweet.id),
                "username":   tweet.user.username,
                "date":       tweet.date.isoformat(),
                "text":       tweet.rawContent or "",
                "hashtags":   tweet.hashtags or [],
                "cashtags":   tweet.cashtags or [],
                "likes":      tweet.likeCount,
                "retweets":   tweet.retweetCount,
                "url":        tweet.url or "",
                "is_retweet": False,
                "retweet_amplification": 0,
                "retweeters": set(),
            })

    # Merge: original tweets that were retweeted get their amplification score
    tweet_id_map = {t["id"]: t for t in raw_tweets}
    for orig_id, rt_data in retweet_map.items():
        if orig_id in tweet_id_map:
            # Original was also fetched directly — update its amplification
            tweet_id_map[orig_id]["retweet_amplification"] = rt_data["retweet_amplification"]
            tweet_id_map[orig_id]["retweeters"] = rt_data["retweeters"]
        else:
            # Original not in our window — add it from retweet data
            raw_tweets.append(rt_data)

    # Serialise sets to lists
    for t in raw_tweets:
        t["retweeters"] = list(t.get("retweeters", set()))

    logger.info(
        f"Tweets: {len(raw_tweets)} unique "
        f"({len(retweet_map)} retweet originals deduplicated, "
        f"saved from inflating mention counts)"
    )

    # Build retweet count map for report use
    retweet_counts = {
        orig_id: data["retweet_amplification"]
        for orig_id, data in retweet_map.items()
    }

    return raw_tweets, retweet_counts


def run() -> Dict:
    """Entry point — returns {members, tweets, retweet_counts, stats}."""
    async def _main():
        api = await _setup_api()
        members = await _fetch_members(api)
        tweets, retweet_counts = await _fetch_tweets(api)
        return {
            "members":        members,
            "tweets":         tweets,
            "retweet_counts": retweet_counts,
            "stats": {
                "member_count":  len(members),
                "tweet_count":   len(tweets),
                "rt_dedupe_count": len(retweet_counts),
            }
        }

    return asyncio.run(_main())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    data = run()
    stats = data["stats"]
    print(f"Members: {stats['member_count']}")
    print(f"Unique tweets: {stats['tweet_count']}")
    print(f"Retweets deduplicated: {stats['rt_dedupe_count']}")
