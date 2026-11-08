"""Meta Model API client shared by every agent.

All reasoning goes through here, using the OpenAI Python SDK pointed at Meta's
endpoint. API keys stay in the agent processes and are never passed into test
containers. Settings come from .env:
  META_API_KEY            required
  META_API_BASE_URL       default https://api.meta.ai/v1
  META_MODEL              default muse-spark-1.3
  META_REASONING_EFFORT   minimal | low | medium | high, default low

muse-spark is a reasoning model: its hidden reasoning tokens count toward
max_tokens. Callers pass the size of the *answer* they need; chat() adds
REASONING_HEADROOM on top so the answer isn't cut off by the reasoning.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from openai import OpenAI

from config import require_env

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_REASONING_EFFORT = "low"
REASONING_HEADROOM = 12000


class EmptyReply(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(
        api_key=require_env("META_API_KEY"),
        base_url=os.environ.get("META_API_BASE_URL") or DEFAULT_BASE_URL,
    )


def model_name() -> str:
    return os.environ.get("META_MODEL") or DEFAULT_MODEL


def reasoning_effort() -> str:
    return os.environ.get("META_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT


def model_settings() -> dict:
    """Model details to save with each verification run (no secrets)."""
    return {
        "provider": "meta",
        "base_url": os.environ.get("META_API_BASE_URL") or DEFAULT_BASE_URL,
        "model": model_name(),
        "reasoning_effort": reasoning_effort(),
    }


def chat(system: str, user: str, max_tokens: int = 2000) -> str:
    """Send a system + user prompt to the Meta Model API and return the reply text.

    max_tokens is the answer budget; reasoning headroom is added on top.
    Raises EmptyReply if the model used the whole budget without answering.
    """
    response = _client().chat.completions.create(
        model=model_name(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens + REASONING_HEADROOM,
        reasoning_effort=reasoning_effort(),
    )
    choice = response.choices[0]
    text = choice.message.content or ""
    if not text.strip():
        details = getattr(response.usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None)
        raise EmptyReply(
            f"Model returned no answer (finish_reason={choice.finish_reason}, "
            f"reasoning_tokens={reasoning}, budget={max_tokens + REASONING_HEADROOM})"
        )
    return text


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
