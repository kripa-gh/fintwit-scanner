"""
claude_client.py
Shared Anthropic API client for all Claude-powered modules.

Features:
  - Automatic retry with exponential backoff
  - Token usage tracking per run
  - Structured JSON output helper
  - Image (base64) support for chart analysis
  - Cost estimation (Sonnet 4.6 pricing)
"""

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Union

import requests

logger = logging.getLogger(__name__)

API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
MODEL     = "claude-sonnet-4-6"
API_URL   = "https://api.anthropic.com/v1/messages"
MAX_RETRIES = 3

# Cost tracking (accumulated per run). Updated from multiple threads during
# parallel enrichment, so all mutations go through _stats_lock — `dict[k] += n`
# is a non-atomic read-modify-write and would otherwise lose updates.
_run_stats = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "errors": 0}
_stats_lock = threading.Lock()

# Sonnet 4.6 pricing per million tokens
INPUT_COST_PER_MTK  = 3.0   # $3.00
OUTPUT_COST_PER_MTK = 15.0  # $15.00


def _headers() -> Dict:
    return {
        "x-api-key":         API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }


def call(
    prompt: str,
    system: str = "",
    max_tokens: int = 1000,
    image_base64: Optional[str] = None,
    image_media_type: str = "image/png",
    expect_json: bool = False,
    temperature: float = 0.2,
) -> Optional[str]:
    """
    Make a Claude API call. Returns response text or None on failure.

    Args:
        prompt:          User message
        system:          System prompt
        max_tokens:      Max output tokens (default 1000)
        image_base64:    Base64-encoded image for vision calls
        image_media_type: MIME type of image
        expect_json:     If True, strips markdown fences before returning
        temperature:     0.0–1.0 (lower = more deterministic)
    """
    if not API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — skipping Claude call")
        return None

    # Build content
    content: List[Dict] = []
    if image_base64:
        content.append({
            "type":   "image",
            "source": {
                "type":       "base64",
                "media_type": image_media_type,
                "data":       image_base64,
            }
        })
    content.append({"type": "text", "text": prompt})

    payload = {
        "model":       MODEL,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "messages":    [{"role": "user", "content": content}],
    }
    if system:
        payload["system"] = system

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(API_URL, headers=_headers(), json=payload, timeout=60)

            if resp.status_code == 429:
                wait = 2 ** attempt * 5
                logger.warning(f"Rate limited — waiting {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                logger.warning(f"Claude API {resp.status_code}: {resp.text[:200]}")
                with _stats_lock:
                    _run_stats["errors"] += 1
                return None

            data = resp.json()

            # Track usage (thread-safe)
            usage = data.get("usage", {})
            with _stats_lock:
                _run_stats["input_tokens"]  += usage.get("input_tokens", 0)
                _run_stats["output_tokens"] += usage.get("output_tokens", 0)
                _run_stats["calls"]         += 1

            text = data["content"][0]["text"].strip()

            if expect_json:
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                text = text.strip()

            return text

        except requests.exceptions.Timeout:
            logger.warning(f"Claude timeout (attempt {attempt+1}/{MAX_RETRIES})")
            time.sleep(2 ** attempt)
        except Exception as e:
            logger.warning(f"Claude call error: {e}")
            with _stats_lock:
                _run_stats["errors"] += 1
            return None

    return None


def call_json(
    prompt: str,
    system: str = "",
    max_tokens: int = 1000,
    image_base64: Optional[str] = None,
    fallback: Any = None,
) -> Any:
    """
    Call Claude expecting JSON output. Returns parsed dict/list or fallback.
    """
    text = call(
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        image_base64=image_base64,
        expect_json=True,
        temperature=0.1,   # lower temp for structured output
    )
    if not text:
        return fallback

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed: {e} | Response: {text[:200]}")
        return fallback


def get_run_stats() -> Dict:
    """Return token usage and estimated cost for this run."""
    input_cost  = (_run_stats["input_tokens"]  / 1_000_000) * INPUT_COST_PER_MTK
    output_cost = (_run_stats["output_tokens"] / 1_000_000) * OUTPUT_COST_PER_MTK
    total_cost  = input_cost + output_cost

    return {
        **_run_stats,
        "input_cost_usd":  round(input_cost, 4),
        "output_cost_usd": round(output_cost, 4),
        "total_cost_usd":  round(total_cost, 4),
        "total_cost_inr":  round(total_cost * 84, 2),
    }


def reset_run_stats() -> None:
    """Reset stats at the start of each run."""
    global _run_stats
    _run_stats = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "errors": 0}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = call("Say hello in one word.", max_tokens=10)
    print("Response:", result)
    print("Stats:", get_run_stats())
