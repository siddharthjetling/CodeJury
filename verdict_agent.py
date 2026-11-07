"""
Verdict Agent — receives all specialist agent results, applies a deterministic
policy gate (PASS / BLOCK / INCONCLUSIVE), then uses the LLM only to write
the human-readable summary. The LLM does not decide the verdict.
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


# ── Deterministic policy gate ─────────────────────────────────────────────────
def apply_policy(results: list[dict]) -> tuple[str, int]:
    """
    Deterministic verdict. LLM does not decide.
    Returns (verdict, risk_score).
    """
    has_critical = False
    has_major = False
    has_minor = False
    total_failures = 0
    any_agent_failed_to_run = False

    for r in results:
        failures = json.loads(r.get("failures_json", "[]"))
        for f in failures:
            total_failures += 1
            sev = f.get("severity", "minor")
            if sev == "critical":
                has_critical = True
            elif sev == "major":
                has_major = True
            else:
                has_minor = True

        # if an agent generated 0 tests, something went wrong
        if r.get("tests_generated", 0) == 0:
            any_agent_failed_to_run = True

    # ── Policy rules (deterministic, not LLM-decided) ─────────────────────
    if any_agent_failed_to_run:
        return "INCONCLUSIVE", 50

    if has_critical:
        risk = min(100, 70 + total_failures * 5)
        return "BLOCK", risk

    if has_major:
        risk = min(100, 40 + total_failures * 10)
        return "BLOCK", risk

    if has_minor:
        risk = min(39, 20 + total_failures * 5)
        return "PASS", risk

    return "PASS", 0


# ── LLM summary (explains the verdict, does not decide it) ───────────────────
SUMMARY_SYSTEM = """You are writing a short summary for a code verification verdict.

You receive:
- The verdict (PASS, BLOCK, or INCONCLUSIVE) — already decided, do not change it
- The risk score — already calculated, do not change it
- Agent results with any failures found

Write ONE sentence summarizing:
- If PASS: what was checked and why it's safe
- If BLOCK: what the most serious defect is and why it blocks the merge
- If INCONCLUSIVE: which check failed to run and why a rerun is needed

Do NOT change the verdict or risk score. Just write the summary sentence. Return only the sentence, nothing else."""


@protocol.on_message(VerdictRequest)
async def handle_verdict(ctx: Context, sender: str, msg: VerdictRequest):
    ctx.logger.info(f"Verdict requested for session {msg.session_id}")

    verdict = "BLOCK"
    risk_score = 50
    summary = "Unable to produce verdict — defaulting to BLOCK for safety."
    evidence_json = "{}"

    try:
        results = json.loads(msg.results_json)

        # ── Step 1: Deterministic policy gate ─────────────────────────────
        verdict, risk_score = apply_policy(results)
        ctx.logger.info(f"Policy gate: {verdict} (risk={risk_score})")

        # ── Step 2: Build evidence structure ──────────────────────────────
        evidence = {}
        for r in results:
            agent_name = r.get("agent_name", "Unknown")
            failures = json.loads(r.get("failures_json", "[]"))
            evidence[agent_name] = {
                "tests_generated": r.get("tests_generated", 0),
                "tests_passed":    r.get("tests_passed", 0),
                "tests_failed":    r.get("tests_failed", 0),
                "failures":        failures,
            }
        evidence_json = json.dumps(evidence)

        # ── Step 3: LLM writes the human summary (does not decide) ───────
        try:
            summary_input = (
                f"Verdict: {verdict}\n"
                f"Risk score: {risk_score}\n"
                f"PR: {msg.pr_url}\n"
                f"Agent results:\n{json.dumps(results, indent=2)}"
            )
            summary = llm.chat(SUMMARY_SYSTEM, summary_input, max_tokens=200).strip()
        except Exception:
            ctx.logger.exception("LLM summary failed — using default")
            if verdict == "PASS":
                summary = f"All required checks passed across {len(results)} agents."
            elif verdict == "INCONCLUSIVE":
                summary = "One or more required checks failed to run. Rerun recommended."
            else:
                summary = f"Blocking defects found. {risk_score}/100 risk score."

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
