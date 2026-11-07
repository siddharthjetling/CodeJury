"""
Shared Meta Model API client for all CodeJury agents.
Uses the OpenAI Python SDK pointed at Meta's endpoint.
API keys stay in agent processes — never passed into test containers.
"""
import os
from openai import OpenAI

META_API_KEY      = os.environ.get("META_API_KEY", "")
META_API_BASE_URL = os.environ.get("META_API_BASE_URL", "https://api.meta.ai/v1")
META_MODEL        = os.environ.get("META_MODEL", "llama-4-maverick")

client = OpenAI(base_url=META_API_BASE_URL, api_key=META_API_KEY)

def ask(system: str, user: str, max_tokens: int = 2000) -> str:
    """Send a system + user prompt to the Meta Model API and return the response text."""
    r = client.chat.completions.create(
        model=META_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=max_tokens,
    )
    return str(r.choices[0].message.content)
