"""
Performance Agent: runs when the change touches database access or loops over data.

Writes pytest tests from the spec only (never the code) that count database
queries at a small and a large data size and assert the spec's limits. Uses
query counts, not timing, so results are deterministic. Runs them, plus the
verifier's protected performance tests, in the Docker sandbox. See specialist.py.
"""
import config  # noqa: F401  (loads .env and the event-loop fix first)
from specialist import make_agent

agent = make_agent("performance", port=8004, seed_env="PERFORMANCE_AGENT_SEED")

if __name__ == "__main__":
    print(f"\n📈 Performance Agent address: {agent.address}\n")
    print("   → Copy this address into config.py as PERFORMANCE_AGENT_ADDRESS\n")
    agent.run()
