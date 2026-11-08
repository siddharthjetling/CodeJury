"""
Verdict Agent: applies the deterministic policy gate (policy_gate.py) to every
check, then asks the LLM for a one-sentence summary. The LLM never decides or
changes the verdict; if it fails, a fixed sentence is used instead.
"""
import asyncio
import json

from uagents import Agent, Context, Protocol

import jury
from config import require_env
from models import VerdictRequest, VerdictResponse

agent = Agent(
    name="codejury-verdict",
    seed=require_env("VERDICT_AGENT_SEED"),
    port=8005,
    mailbox=True,
    publish_agent_details=True,
)

protocol = Protocol(name="codejury-internal", version="1.0.0")


@protocol.on_message(VerdictRequest)
async def handle_verdict(ctx: Context, sender: str, msg: VerdictRequest):
    ctx.logger.info(f"Verdict requested for run {msg.run_id}")
    try:
        checks = json.loads(msg.checks_json)
        routing = json.loads(msg.routing_json)
        record = await asyncio.to_thread(jury.decide, msg.run_id, msg.ref, checks, routing)
    except Exception as e:
        ctx.logger.exception("Verdict failed")
        record = {
            "gate": {"verdict": "INCONCLUSIVE", "reasons": [f"Verdict agent error: {type(e).__name__}"]},
            "evidence": {},
            "summary": "The verdict could not be computed, so the merge is held.",
        }

    ctx.logger.info(f"Policy gate: {record['gate']['verdict']}")
    await ctx.send(sender, VerdictResponse(
        session_id=msg.session_id,
        run_id=msg.run_id,
        verdict=record["gate"]["verdict"],
        summary=record["summary"],
        reasons_json=json.dumps(record["gate"]["reasons"]),
        evidence_json=json.dumps(record["evidence"]),
    ))


agent.include(protocol)

if __name__ == "__main__":
    print(f"\n⚖️  Verdict Agent address: {agent.address}\n")
    print("   → Copy this address into config.py as VERDICT_AGENT_ADDRESS\n")
    agent.run()
