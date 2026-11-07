"""LLM client shared by every CodeJury agent.

Uses ASI-1 via the OpenAI Python SDK.
"""
from __future__ import annotations

import json
import os

from openai import OpenAI

# Read from .env via config, but hardcode fallbacks that actually work
from config import require_env

_CLIENT = None

def _client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        api_key = os.environ.get("META_API_KEY", "") or require_env("META_API_KEY")
        base_url = os.environ.get("META_API_BASE_URL", "") or "https://api.asi1.ai/v1"
        # Force ASI-1 if Meta URL is set (it 404s)
        if "meta.ai" in base_url:
            base_url = "https://api.asi1.ai/v1"
        _CLIENT = OpenAI(api_key=api_key, base_url=base_url)
    return _CLIENT


def model_name() -> str:
    m = os.environ.get("META_MODEL", "") or "asi1"
    if m in ("muse-spark-1.3", "muse-wonder-1.0"):
        return "asi1"
    return m


def model_settings() -> dict:
    return {"provider": "asi1", "base_url": "https://api.asi1.ai/v1", "model": model_name()}


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


ask = chat


def chat_json(system: str, user: str, max_tokens: int = 4000) -> dict:
    """Call the model and parse the first JSON object in its reply.
    Retries once with higher max_tokens if JSON is truncated.
    """
    for attempt in range(2):
        raw = chat(system, user, max_tokens=max_tokens)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= start:
            max_tokens = min(max_tokens * 2, 8000)
            continue
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            max_tokens = min(max_tokens * 2, 8000)
            continue
    return {}
