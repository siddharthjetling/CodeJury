"""LLM client shared by every CodeJury agent.

All reasoning goes through here, using the OpenAI Python SDK. The provider is an
explicit setting in .env and is never switched silently:

  LLM_PROVIDER            meta (default) | asi1

  meta:  META_API_KEY            required
         META_API_BASE_URL       default https://api.meta.ai/v1
         META_MODEL              default muse-spark-1.3
         META_REASONING_EFFORT   minimal | low | medium | high, default low

  asi1:  ASI1_API_KEY            required
         ASI1_API_BASE_URL       default https://api.asi1.ai/v1
         ASI1_MODEL              default asi1

API keys stay in the agent processes and are never passed into test containers.

muse-spark is a reasoning model: its hidden reasoning tokens count toward
max_tokens. Callers pass the size of the *answer* they need; for Meta, chat()
adds REASONING_HEADROOM on top so the answer isn't cut off by the reasoning.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from openai import OpenAI

from config import require_env

PROVIDERS = {
    "meta": {
        "key_env": "META_API_KEY",
        "url_env": "META_API_BASE_URL", "default_url": "https://api.meta.ai/v1",
        "model_env": "META_MODEL", "default_model": "muse-spark-1.3",
        "reasoning": True,
    },
    "asi1": {
        "key_env": "ASI1_API_KEY",
        "url_env": "ASI1_API_BASE_URL", "default_url": "https://api.asi1.ai/v1",
        "model_env": "ASI1_MODEL", "default_model": "asi1",
        "reasoning": False,
    },
}
DEFAULT_PROVIDER = "meta"
DEFAULT_REASONING_EFFORT = "low"
REASONING_HEADROOM = 12000


class EmptyReply(RuntimeError):
    pass


def provider() -> str:
    name = (os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise RuntimeError(f"LLM_PROVIDER={name!r} is not supported. Use one of: {', '.join(PROVIDERS)}.")
    return name


def _settings() -> dict:
    return PROVIDERS[provider()]


def base_url() -> str:
    s = _settings()
    return os.environ.get(s["url_env"]) or s["default_url"]


def model_name() -> str:
    s = _settings()
    return os.environ.get(s["model_env"]) or s["default_model"]


def reasoning_effort() -> str:
    return os.environ.get("META_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT


@lru_cache(maxsize=None)
def _client(name: str) -> OpenAI:
    s = PROVIDERS[name]
    return OpenAI(api_key=require_env(s["key_env"]), base_url=os.environ.get(s["url_env"]) or s["default_url"])


def model_settings() -> dict:
    """Model details to save with each verification run (no secrets)."""
    out = {"provider": provider(), "base_url": base_url(), "model": model_name()}
    if _settings()["reasoning"]:
        out["reasoning_effort"] = reasoning_effort()
    return out


def chat(system: str, user: str, max_tokens: int = 2000) -> str:
    """Send a system + user prompt and return the reply text.

    max_tokens is the answer budget. Raises EmptyReply if the model returns no text.
    """
    name = provider()
    kwargs = {}
    budget = max_tokens
    if PROVIDERS[name]["reasoning"]:
        budget += REASONING_HEADROOM
        kwargs["reasoning_effort"] = reasoning_effort()
    response = _client(name).chat.completions.create(
        model=model_name(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=budget,
        **kwargs,
    )
    choice = response.choices[0]
    text = choice.message.content or ""
    if not text.strip():
        details = getattr(response.usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None)
        raise EmptyReply(
            f"{name} returned no answer (finish_reason={choice.finish_reason}, "
            f"reasoning_tokens={reasoning}, budget={budget})"
        )
    return text


# Older name used by some agent code.
ask = chat


def chat_json(system: str, user: str, max_tokens: int = 4000) -> dict:
    """Call the model and parse the first JSON object in its reply.

    Retries once with a larger budget if the JSON is missing or truncated.
    Returns an empty dict if both attempts fail.
    """
    for _ in range(2):
        raw = chat(system, user, max_tokens=max_tokens)
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass
        max_tokens = min(max_tokens * 2, 8000)
    return {}
