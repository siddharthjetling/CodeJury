"""
Correctness Agent — the mandatory baseline juror in CodeJury.
Always fires on every PR. Reads the spec/issue ONLY, generates behavioral
test cases covering:
  1. Edge cases (null, empty, boundary, type coercion, overflow)
  2. Logic paths (branch coverage, off-by-one, boolean conditions)
  3. State transitions (ordering, idempotency, side effects)
  4. Contract violations (return type, shape, missing fields)

Then runs those tests conceptually against the PR diff to find mismatches.

This agent NEVER generates tests from reading the code first.
The spec is the sole source of truth — this is the independence guarantee.
"""
import json
from uagents import Agent, Context, Protocol

from models import InspectRequest, InspectResponse
from llm import ask

agent = Agent(
    name="codejury-correctness",
    seed="codejury correctness agent aie summit 2026",
    port=8002,
    mailbox=True,
    publish_agent_details=True,
)

protocol = Protocol(name="codejury-internal", version="1.0.0")

# ── PHASE 1: Generate tests from spec alone ──────────────────────────────────
GENERATE_TESTS_SYSTEM = """You are a senior test engineer. You receive a SPECIFICATION (GitHub issue or PR description) for a code change.

Your job: generate a comprehensive set of behavioral test cases from the spec ALONE.
You have NOT seen the implementation. You are testing what the code SHOULD do, not what it does.

Generate tests across these categories:

1. EDGE CASES — inputs the developer probably didn't think about:
   - null / undefined / None inputs for every parameter
   - empty strings, empty arrays, empty objects
   - zero, negative numbers, MAX_INT, floating point precision
   - unicode, emoji, special characters in strings
   - extremely long inputs (100k chars, 1M array elements)
   - type mismatches (string where number expected, array where object expected)

2. LOGIC PATHS — branches and conditions:
   - every conditional in the spec tested in both directions
   - boundary values (if "up to 100" → test 99, 100, 101)
   - off-by-one on loops, pagination, indexing
   - combinations of optional parameters (all present, none present, partial)
   - default values — what happens when optional params are missing

3. STATE & ORDERING:
   - calling the same operation twice (idempotency)
   - calling operations out of expected order
   - concurrent calls with same inputs
   - side effects — does the function modify inputs it shouldn't?
   - cleanup on failure — if step 3 of 5 fails, are steps 1-2 rolled back?

4. CONTRACT:
   - return type matches what spec promises
   - all fields in the response that spec mentions are present
   - error responses follow expected format
   - HTTP status codes match spec (if applicable)

Return ONLY a valid JSON object:
{
  "tests": [
    {
      "name": "<short descriptive test name>",
      "category": "edge_case|logic|state|contract",
      "input": "<what input or scenario to test>",
      "expected": "<what the spec says should happen>",
      "why_matters": "<what breaks if this fails — in plain English>"
    }
  ]
}

Rules:
- Generate 8-15 tests. Quality over quantity.
- Every test must trace back to something the spec says or implies.
- If the spec is vague on a behavior, still test it but note it as "spec unclear — testing defensive behavior".
- Never generate trivial happy-path-only tests. A junior engineer can do that. You find what they miss.
- Think like an attacker, not a developer."""


# ── PHASE 2: Run tests conceptually against the code ─────────────────────────
RUN_TESTS_SYSTEM = """You are a code analysis engine. You receive:
1. A set of behavioral test cases (generated from the spec, without seeing the code)
2. The actual PR diff (the code being reviewed)

Your job: for each test case, determine if the code handles it correctly.

For each test, analyze the PR diff and determine:
- PASS: the code explicitly handles this case correctly
- FAIL: the code does NOT handle this case, or handles it incorrectly
- UNCERTAIN: can't determine from the diff alone (code may handle it in untouched files)

Return ONLY a valid JSON object:
{
  "results": [
    {
      "test_name": "<name from the test>",
      "result": "PASS" | "FAIL" | "UNCERTAIN",
      "reason": "<specific line or pattern in the diff that proves this, or why it's missing>",
      "severity": "critical" | "major" | "minor"
    }
  ]
}

Severity guide:
- critical: data loss, crash, security hole, undefined behavior in production
- major: wrong output, broken business logic, missing error handling that will cause 500s
- minor: missing validation that's unlikely to be hit, style issues, missing but non-critical edge case

Rules:
- Only mark FAIL if you can explain specifically what's wrong or missing in the diff.
- UNCERTAIN is honest — don't guess PASS just because you can't find a bug.
- Reference specific lines or patterns from the diff in your reasons.
- A missing null check on user input is "major", not "minor" — users WILL send null.
- A missing handler for MAX_INT is "minor" unless the spec explicitly mentions large numbers."""


@protocol.on_message(InspectRequest)
async def handle_inspect(ctx: Context, sender: str, msg: InspectRequest):
    ctx.logger.info(f"Correctness: inspecting PR {msg.pr_url} for session {msg.session_id}")

    tests_generated = 0
    tests_passed = 0
    tests_failed = 0
    failures_json = "[]"
    clean = True

    try:
        # ── PHASE 1: Generate tests from spec only ────────────────────────
        ctx.logger.info("Phase 1: Generating tests from spec...")

        gen_raw = ask(
            system=GENERATE_TESTS_SYSTEM,
            user=f"SPECIFICATION:\n\n{msg.spec_text[:3000]}",
            max_tokens=2000,
        )

        gen_start = gen_raw.find("{")
        gen_end = gen_raw.rfind("}") + 1
        gen_data = json.loads(gen_raw[gen_start:gen_end])
        tests = gen_data.get("tests", [])
        tests_generated = len(tests)

        ctx.logger.info(f"Phase 1 complete: {tests_generated} tests generated")

        if tests_generated == 0:
            await ctx.send(sender, InspectResponse(
                session_id=msg.session_id,
                agent_name="Correctness",
                tests_generated=0, tests_passed=0, tests_failed=0,
                failures_json="[]", clean=True,
            ))
            return

        # ── PHASE 2: Run tests against the PR diff ───────────────────────
        ctx.logger.info("Phase 2: Running tests against PR diff...")

        run_content = (
            f"TEST CASES (generated from spec without seeing code):\n"
            f"{json.dumps(tests, indent=2)}\n\n"
            f"PR DIFF (the actual code to analyze):\n"
            f"{msg.pr_diff[:5000]}"
        )

        run_raw = ask(
            system=RUN_TESTS_SYSTEM,
            user=run_content,
            max_tokens=2000,
        )

        run_start = run_raw.find("{")
        run_end = run_raw.rfind("}") + 1
        run_data = json.loads(run_raw[run_start:run_end])
        results = run_data.get("results", [])

        # ── Tally results ─────────────────────────────────────────────────
        failures = []
        for r in results:
            if r.get("result") == "PASS":
                tests_passed += 1
            elif r.get("result") == "FAIL":
                tests_failed += 1
                failures.append({
                    "test":     r.get("test_name", "unnamed"),
                    "reason":   r.get("reason", "no reason provided"),
                    "severity": r.get("severity", "minor"),
                })

        clean = tests_failed == 0
        failures_json = json.dumps(failures)

        ctx.logger.info(
            f"Phase 2 complete: {tests_passed} passed, {tests_failed} failed, "
            f"{tests_generated - tests_passed - tests_failed} uncertain"
        )

    except Exception:
        ctx.logger.exception("Correctness inspection failed")

    await ctx.send(sender, InspectResponse(
        session_id=msg.session_id,
        agent_name="Correctness",
        tests_generated=tests_generated,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        failures_json=failures_json,
        clean=clean,
    ))


agent.include(protocol)

if __name__ == "__main__":
    print(f"\n🧪 Correctness Agent address: {agent.address}\n")
    print("   → Copy this address into config.py as CORRECTNESS_AGENT_ADDRESS\n")
    agent.run()
