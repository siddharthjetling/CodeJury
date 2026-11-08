"""
Security Agent: runs when the change touches access control, credentials or
customer data.

Thinks like an attacker: writes pytest tests from the spec only (never the code)
that try to read another customer's data, tamper with IDs, or skip the API key.
Runs them, plus the verifier's protected security tests, in the Docker sandbox.
A finding counts only if a test actually reproduces it. See specialist.py.
"""
import config  # noqa: F401  (loads .env and the event-loop fix first)
from specialist import make_agent

agent = make_agent("security", port=8003, seed_env="SECURITY_AGENT_SEED")

if __name__ == "__main__":
    print(f"\n🛡️  Security Agent address: {agent.address}\n")
    print("   → Copy this address into config.py as SECURITY_AGENT_ADDRESS\n")
    agent.run()
