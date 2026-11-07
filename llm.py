"""Meta Model API client shared by every agent.

All reasoning goes through here. Settings come from .env:
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
    response = _client().chat.completions.create(
        model=model_name(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def chat_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    """Call the model and parse the first JSON object in its reply.

    Raises ValueError if the reply contains no valid JSON object.
    """
    raw = chat(system, user, max_tokens)
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"Model reply had no JSON object: {raw[:200]!r}")
    return json.loads(raw[start:end])
