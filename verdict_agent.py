"""
Verdict Agent — receives all specialist agent results, does deep analysis
on agents that found issues, produces final APPROVE/BLOCK verdict with
risk score and full evidence chain.
"""
import json
from uagents import Agent, Context, Protocol

import llm
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

VERDICT_SYSTEM = """You are the final judge in CodeJury — an independent code verification system.

You receive test results from multiple specialist agents that independently analyzed an AI-generated PR.
Each agent generated tests from the SPEC ONLY (never saw the code) and ran them against the PR.

Your job:
1. Analyze all agent results
2. For agents that found failures — do a DEEP ANALYSIS:
   - Confirm the failure is a real defect (not a false positive)
   - Rate severity: "critical" (security/data loss), "major" (logic error/crash), "minor" (style/edge case)
   - Explain WHY it's a real problem in plain English
3. Produce a final verdict: APPROVE or BLOCK
4. Calculate a risk score 0–100:
   - 0 = perfectly safe to merge
   - 100 = critical defects, must not merge
   - Any critical failure = minimum 70
   - Any major failure = minimum 40
   - All clean = 0–15 depending on test coverage

Return ONLY a valid JSON object:
{
  "verdict": "APPROVE" or "BLOCK",
  "risk_score": <0-100>,
  "summary": "<one sentence explaining the verdict>",
  "evidence": {
    "<agent_name>": {
      "tests_generated": <int>,
      "failures": [
        {"test": "<test name>", "reason": "<why this is a real defect>", "severity": "critical|major|minor"}
      ]
    }
  }
}

Rules:
- If ANY critical failure exists → verdict MUST be BLOCK
- If ANY major failure exists → verdict MUST be BLOCK
- If only minor failures → verdict can be APPROVE with risk_score 20-39
- If all agents are clean → verdict is APPROVE with risk_score 0-15
- Never invent failures that agents didn't report
- Keep reasons concise — one sentence each"""


@protocol.on_message(VerdictRequest)
async def handle_verdict(ctx: Context, sender: str, msg: VerdictRequest):
    ctx.logger.info(f"Verdict requested for session {msg.session_id}")

    verdict = "BLOCK"
    risk_score = 50
    summary = "Unable to produce verdict — defaulting to BLOCK for safety."
    evidence_json = "{}"

    try:
        results = json.loads(msg.results_json)

        user_content = (
            f"PR: {msg.pr_url}\n\n"
            f"Spec:\n{msg.spec_text[:2000]}\n\n"
            f"Agent Results:\n{json.dumps(results, indent=2)}"
        )

        data = llm.chat_json(VERDICT_SYSTEM, user_content, max_tokens=1500)

        verdict      = data.get("verdict", "BLOCK").upper()
        risk_score   = int(data.get("risk_score", 50))
        summary      = data.get("summary", "No summary provided.")
        evidence     = data.get("evidence", {})
        evidence_json = json.dumps(evidence)

        # safety: if any critical/major failure, force BLOCK
        for agent_data in evidence.values():
            for f in agent_data.get("failures", []):
                if f.get("severity") in ("critical", "major"):
                    verdict = "BLOCK"

        # clamp risk score
        risk_score = max(0, min(100, risk_score))

    except Exception:
        ctx.logger.exception("Verdict generation failed")

    await ctx.send(sender, VerdictResponse(
        session_id=msg.session_id,
        verdict=verdict,
        risk_score=risk_score,
        summary=summary,
        evidence_json=evidence_json,
    ))


agent.include(protocol)

if __name__ == "__main__":
    print(f"\n⚖️  Verdict Agent address: {agent.address}\n")
    print("   → Copy this address into config.py as VERDICT_AGENT_ADDRESS\n")
    agent.run()
