from uagents import Model

# ── Orchestrator ← User (via ASI:One) ─────────────────────────────────────────
class VerifyRequest(Model):
    session_id: str
    text:       str   # raw user message — contains PR URL and optional spec URL

# ── Orchestrator → Specialist Agents (BehaviorAgent, SecurityAgent) ───────────
class InspectRequest(Model):
    session_id: str
    spec_text:  str   # extracted GitHub issue / spec content
    pr_diff:    str   # extracted PR code diff
    pr_url:     str   # original PR URL for reference

# ── Specialist Agents → Orchestrator ─────────────────────────────────────────
class InspectResponse(Model):
    session_id:      str
    agent_name:      str   # "BehaviorAgent" | "SecurityAgent"
    tests_generated: int
    tests_passed:    int
    tests_failed:    int
    failures_json:   str   # JSON list of {test, reason, severity: "critical"|"major"|"minor"}
    clean:           bool  # True if zero failures — agent stops here, no deep dive needed

# ── Orchestrator → Verdict Agent ──────────────────────────────────────────────
class VerdictRequest(Model):
    session_id:   str
    pr_url:       str
    spec_text:    str
    results_json: str   # JSON list of all InspectResponse data from all agents

# ── Verdict Agent → Orchestrator ──────────────────────────────────────────────
class VerdictResponse(Model):
    session_id:    str
    verdict:       str   # "APPROVE" | "BLOCK"
    risk_score:    int   # 0–100
    summary:       str   # one-line reason for verdict
    evidence_json: str   # JSON: {agent_name: {tests_generated, failures: [{test, reason, severity}]}}
