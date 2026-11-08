"""
Correctness Agent: the mandatory juror. Runs on every change.

Writes pytest tests from the spec only (never the code) covering response shape,
ordering, computed values, empty and boundary cases, and status codes. Runs them,
plus the verifier's protected correctness tests, in the Docker sandbox and
returns executed results as evidence. See specialist.py for the shared flow.
"""
import config  # noqa: F401  (loads .env and the event-loop fix first)
from specialist import make_agent

agent = make_agent("correctness", port=8002, seed_env="CORRECTNESS_AGENT_SEED")

if __name__ == "__main__":
    print(f"\n🧪 Correctness Agent address: {agent.address}\n")
    print("   → Copy this address into config.py as CORRECTNESS_AGENT_ADDRESS\n")
    agent.run()
