import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# uAgents calls asyncio.get_event_loop() when an Agent is created, which raises
# on Python 3.14+ if no loop exists yet. Every agent imports config first.
if sys.version_info >= (3, 14):
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

# Secrets (API keys, agent seeds) live in .env, which is gitignored.
# See .env.example for the full list.
load_dotenv(Path(__file__).resolve().parent / ".env")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to .env (see .env.example).")
    return value


# ── Sub-agent addresses ───────────────────────────────────────────────────────
# Addresses are public, so they can stay in code.
# Step 1: Run each sub-agent once (python correctness_agent.py, etc.)
# Step 2: Copy the printed address into each variable below
# Step 3: Restart orchestrator.py

CORRECTNESS_AGENT_ADDRESS  = "agent1q..."   # from correctness_agent.py output
SECURITY_AGENT_ADDRESS     = "agent1q..."   # from security_agent.py output
PERFORMANCE_AGENT_ADDRESS  = "agent1q..."   # from performance_agent.py output (Siddharth)
VERDICT_AGENT_ADDRESS      = "agent1q..."   # from verdict_agent.py output
