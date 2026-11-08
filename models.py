from uagents import Model

# A "check" (inside the *_json fields) is one test result in the policy gate format:
#   {"id", "category", "source": "repository"|"protected"|"generated"|"agent",
#    "status": "passed"|"failed"|"error"|"timeout", "message", "evidence"}


# ── Orchestrator → Specialist Agents (Correctness, Security, Performance) ────
class InspectRequest(Model):
    session_id: str   # same as run_id
    run_id:     str
    ref:        str   # demo repo branch being verified
    spec_text:  str   # task requirements; the only input used to write tests
    pr_diff:    str   # git diff base...ref (for routing context; not used to write tests)
    pr_url:     str   # display label for the change


# ── Specialist Agents → Orchestrator ─────────────────────────────────────────
class InspectResponse(Model):
    session_id:   str
    run_id:       str
    agent_name:   str   # "Correctness" | "Security" | "Performance"
    category:     str   # "correctness" | "security" | "performance"
    error:        str   # empty unless the agent itself crashed
    tests_run:    int
    tests_passed: int
    tests_failed: int
    checks_json:  str   # JSON list of checks
    notes_json:   str   # JSON list of strings (discarded tests, generation problems)


# ── Orchestrator → Verdict Agent ──────────────────────────────────────────────
class VerdictRequest(Model):
    session_id:   str
    run_id:       str
    ref:          str
    pr_url:       str
    checks_json:  str   # baseline + all specialist checks
    routing_json: str   # JSON list of {category, reason}


# ── Verdict Agent → Orchestrator ──────────────────────────────────────────────
class VerdictResponse(Model):
    session_id:    str
    run_id:        str
    verdict:       str   # "PASS" | "BLOCK" | "INCONCLUSIVE" (decided by policy_gate.py)
    summary:       str   # LLM-written sentence; never changes the verdict
    reasons_json:  str   # JSON list of strings from the policy gate
    evidence_json: str   # JSON: {agent_name: {tests_generated, tests_passed, tests_failed,
                         #         failures: [{test, check_id, source, reason, severity}]}}
