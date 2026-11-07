"""Meta Model API client shared by every agent.

All reasoning goes through here, using the OpenAI Python SDK pointed at Meta's
endpoint. API keys stay in the agent processes and are never passed into test
containers. Settings come from .env:
  META_API_KEY       required
  META_API_BASE_URL  default https://api.meta.ai/v1
  META_MODEL         default muse-spark-1.3
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from openai import OpenAI

from config import require_env

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(
        api_key=require_env("META_API_KEY"),
        base_url=os.environ.get("META_API_BASE_URL") or DEFAULT_BASE_URL,
    )


def model_name() -> str:
    return os.environ.get("META_MODEL") or DEFAULT_MODEL


def model_settings() -> dict:
    """Model details to save with each verification run (no secrets)."""
    return {
        "provider": "meta",
        "base_url": os.environ.get("META_API_BASE_URL") or DEFAULT_BASE_URL,
        "model": model_name(),
    }


def chat(system: str, user: str, max_tokens: int = 2000) -> str:
    """Send a system + user prompt to the Meta Model API and return the reply text."""
    response = _client().chat.completions.create(
        model=model_name(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


# Older name used by some agent code.
ask = chat


def chat_json(system: str, user: str, max_tokens: int = 4000) -> dict:
    """Call the model and parse the first JSON object in its reply.

    Retries once with higher max_tokens if JSON is truncated.
    Returns empty dict on failure instead of raising.
    """
    for attempt in range(2):
        raw = chat(system, user, max_tokens=max_tokens)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= start:
            # no JSON at all — retry with more tokens
            max_tokens = min(max_tokens * 2, 8000)
            continue
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            # truncated JSON — retry with more tokens
            max_tokens = min(max_tokens * 2, 8000)
            continue
    return {}
