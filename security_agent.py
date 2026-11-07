"""
Security Agent — the conditional juror in CodeJury.
Only fires when the orchestrator detects auth/token/session/security keywords in the PR diff.

Two-phase approach (same independence guarantee as BehaviorAgent):
  PHASE 1: Read spec ONLY → generate security-focused test cases
  PHASE 2: Run those tests against the PR diff

This agent thinks like a penetration tester, not a code reviewer.
It doesn't look for "bad code" — it looks for exploitable paths.

Security categories covered:
  1. Authentication bypass — can you skip the auth check entirely?
  2. Authorization escalation — can a regular user do admin things?
  3. Injection — SQL, NoSQL, command, LDAP, XSS, template injection
  4. Secrets exposure — are credentials, tokens, keys exposed in logs/responses/errors?
  5. Session management — fixation, hijacking, infinite sessions, missing expiry
  6. Input trust boundary — is user input trusted without sanitization?
  7. Cryptographic weakness — weak hashing, no salt, ECB mode, hardcoded keys
  8. Race conditions — TOCTOU, double-spend, parallel session abuse
  9. Error information leakage — stack traces, internal paths, DB schema in errors
  10. Dependency risk — known vulnerable patterns, unsafe deserialization
"""
import json
from uagents import Agent, Context, Protocol

import llm
from config import require_env
from models import InspectRequest, InspectResponse

agent = Agent(
    name="codejury-security",
    seed=require_env("SECURITY_AGENT_SEED"),
    port=8003,
    mailbox=True,
    publish_agent_details=True,
)

protocol = Protocol(name="codejury-internal", version="1.0.0")

# ── PHASE 1: Generate security tests from spec alone ─────────────────────────
GENERATE_SECURITY_TESTS = """You are a senior application security engineer conducting a threat-model-driven review.

You receive a SPECIFICATION (GitHub issue or PR description) for a code change.
You have NOT seen the implementation. You generate attack scenarios based on what the feature SHOULD do.

Think like an attacker. For each feature described in the spec, ask:
- "How would I bypass this?"
- "What if I send unexpected data?"
- "What happens if I'm not who I claim to be?"
- "What information leaks on failure?"

Generate test cases across these attack surfaces:

1. AUTHENTICATION BYPASS:
   - Can the endpoint be hit without a token at all?
   - What if the token is expired / malformed / from a different user?
   - Algorithm confusion (JWT with alg:none, RS256→HS256 swap)
   - Empty string as token, null token, token in wrong header

2. AUTHORIZATION ESCALATION:
   - Can a regular user access admin-only operations by changing an ID?
   - IDOR: replace user_id in the request with another user's ID
   - Can a read-only user perform writes?
   - Horizontal privilege: user A accessing user B's data

3. INJECTION:
   - SQL: ' OR 1=1 --, UNION SELECT, stacked queries
   - NoSQL: $gt, $ne operators in JSON fields
   - Command injection: ; rm -rf, $(whoami), backticks
   - XSS: <script>, event handlers, data: URLs in output fields
   - Template injection: {{7*7}}, ${7*7}
   - Path traversal: ../../etc/passwd in file parameters

4. SECRETS & DATA EXPOSURE:
   - Are tokens/passwords logged in plaintext?
   - Do error responses include stack traces, DB queries, internal IPs?
   - Are secrets in environment variables or hardcoded?
   - Is sensitive data returned in responses that shouldn't have it?

5. SESSION & TOKEN MANAGEMENT:
   - Session fixation: can an attacker set the victim's session ID?
   - Missing expiry: can a token live forever?
   - Refresh token rotation: is the old refresh token invalidated?
   - Concurrent sessions: can the same token be used from two IPs?

6. RACE CONDITIONS:
   - TOCTOU: check-then-act with a gap an attacker can exploit
   - Double-submit: send the same request twice simultaneously
   - Balance manipulation: deduct-then-credit with parallel requests

7. CRYPTOGRAPHIC ISSUES:
   - Weak hashing (MD5, SHA1) for passwords
   - Missing salt, predictable IVs, ECB mode
   - Hardcoded encryption keys in source
   - Timing side channels in token comparison (use of == instead of constant-time compare)

Return ONLY a valid JSON object:
{
  "tests": [
    {
      "name": "<short attack scenario name>",
      "attack_surface": "auth_bypass|authz_escalation|injection|secrets|session|race_condition|crypto",
      "attack_vector": "<exact input or action an attacker would try>",
      "expected_defense": "<what the spec implies should prevent this>",
      "impact_if_exploitable": "<what happens if this attack works — in plain English>"
    }
  ]
}

Rules:
- Generate 8-12 security tests. Depth over breadth.
- Every test must be a REALISTIC attack, not a theoretical concern.
- Prioritize attacks that match the feature described in the spec.
- If the spec mentions auth → heavy focus on auth bypass + session management.
- If the spec mentions data storage → heavy focus on injection + secrets.
- If the spec mentions payments → heavy focus on race conditions + authorization.
- DO NOT generate generic OWASP top 10 tests. Generate tests SPECIFIC to this spec."""


# ── PHASE 2: Run security tests against the code ─────────────────────────────
RUN_SECURITY_TESTS = """You are a penetration tester reviewing actual code against a set of attack scenarios.

You receive:
1. Security test cases (generated from the spec, without seeing the code)
2. The actual PR diff (the code being reviewed)

For each attack scenario, analyze the code and determine:
- PASS: the code explicitly defends against this attack (show the defense)
- FAIL: the code is vulnerable to this attack (show the vulnerable pattern)
- UNCERTAIN: can't determine from the diff alone

Return ONLY a valid JSON object:
{
  "results": [
    {
      "test_name": "<name from the test>",
      "result": "PASS" | "FAIL" | "UNCERTAIN",
      "reason": "<specific code pattern that defends or is vulnerable, or why you can't tell>",
      "severity": "critical" | "major" | "minor",
      "exploitability": "<how easy is this to exploit: trivial|moderate|hard>"
    }
  ]
}

Severity guide for security:
- critical: remotely exploitable without auth, data breach, RCE, auth bypass
- major: requires some access but leads to privilege escalation, data leak, or session hijack
- minor: requires specific conditions, low impact, or defense-in-depth issue

Rules:
- Only mark FAIL if you can point to a SPECIFIC vulnerable pattern or a MISSING defense in the diff.
- "No validation visible in the diff" is UNCERTAIN, not FAIL — the validation may exist elsewhere.
- But "user input passed directly to SQL query with string concatenation" is FAIL — that's visible.
- Reference specific lines, function names, or patterns from the diff.
- If the code uses a framework's built-in protection (e.g. parameterized queries, CSRF middleware),
  mark it PASS and cite the framework protection.
- A FAIL with exploitability "trivial" is more urgent than exploitability "hard"."""


@protocol.on_message(InspectRequest)
async def handle_inspect(ctx: Context, sender: str, msg: InspectRequest):
    ctx.logger.info(f"SecurityAgent: inspecting PR {msg.pr_url} for session {msg.session_id}")

    tests_generated = 0
    tests_passed = 0
    tests_failed = 0
    failures_json = "[]"
    clean = True

    try:
        # ── PHASE 1: Generate security tests from spec ────────────────────
        ctx.logger.info("Phase 1: Generating security attack scenarios from spec...")

        gen_data = llm.chat_json(
            GENERATE_SECURITY_TESTS,
            f"SPECIFICATION:\n\n{msg.spec_text[:3000]}",
        )
        tests = gen_data.get("tests", [])
        tests_generated = len(tests)

        ctx.logger.info(f"Phase 1 complete: {tests_generated} attack scenarios generated")

        if tests_generated == 0:
            await ctx.send(sender, InspectResponse(
                session_id=msg.session_id,
                agent_name="SecurityAgent",
                tests_generated=0, tests_passed=0, tests_failed=0,
                failures_json="[]", clean=True,
            ))
            return

        # ── PHASE 2: Run attack scenarios against PR diff ─────────────────
        ctx.logger.info("Phase 2: Testing attack scenarios against PR diff...")

        run_content = (
            f"SECURITY ATTACK SCENARIOS (generated from spec without seeing code):\n"
            f"{json.dumps(tests, indent=2)}\n\n"
            f"PR DIFF (the actual code to analyze for vulnerabilities):\n"
            f"{msg.pr_diff[:5000]}"
        )

        run_data = llm.chat_json(RUN_SECURITY_TESTS, run_content)
        results = run_data.get("results", [])

        # ── Tally results ─────────────────────────────────────────────────
        failures = []
        for r in results:
            if r.get("result") == "PASS":
                tests_passed += 1
            elif r.get("result") == "FAIL":
                tests_failed += 1
                exploit = r.get("exploitability", "unknown")
                reason = r.get("reason", "no reason provided")
                failures.append({
                    "test":     r.get("test_name", "unnamed"),
                    "reason":   f"[{exploit} exploitability] {reason}",
                    "severity": r.get("severity", "major"),
                })
            # UNCERTAIN = honest — neither pass nor fail

        clean = tests_failed == 0
        failures_json = json.dumps(failures)

        ctx.logger.info(
            f"Phase 2 complete: {tests_passed} defended, {tests_failed} vulnerable, "
            f"{tests_generated - tests_passed - tests_failed} uncertain"
        )

    except Exception:
        ctx.logger.exception("SecurityAgent inspection failed")

    await ctx.send(sender, InspectResponse(
        session_id=msg.session_id,
        agent_name="SecurityAgent",
        tests_generated=tests_generated,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        failures_json=failures_json,
        clean=clean,
    ))


agent.include(protocol)

if __name__ == "__main__":
    print(f"\n🛡️  Security Agent address: {agent.address}\n")
    print("   → Copy this address into config.py as SECURITY_AGENT_ADDRESS\n")
    agent.run()
