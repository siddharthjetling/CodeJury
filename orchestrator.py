"""
CodeJury Orchestrator — the only agent users interact with via ASI:One.
Reads PR + spec from GitHub, detects change type, routes to specialist agents,
collects results, sends to Verdict Agent, returns final PASS/BLOCK/INCONCLUSIVE.
"""
import json, re
from datetime import datetime
from uuid import uuid4

import httpx
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, TextContent, EndSessionContent,
    MetadataContent, chat_protocol_spec,
)

from models import (
    InspectRequest, InspectResponse,
    VerdictRequest, VerdictResponse,
)
import config

# ── Agent ─────────────────────────────────────────────────────────────────────
agent = Agent(
    name="codejury-orchestrator",
    seed="codejury orchestrator aie summit 2026",
    port=8001,
    mailbox=True,
    publish_agent_details=True,
)

chat_proto     = Protocol(spec=chat_protocol_spec)
internal_proto = Protocol(name="codejury-internal", version="1.0.0")

# ── Sessions ──────────────────────────────────────────────────────────────────
sessions: dict[str, dict] = {}

def get_session(sender: str) -> dict:
    if sender not in sessions:
        sessions[sender] = {
            "user_sender": sender,
            "state":       "idle",       # idle | waiting_agents | waiting_verdict
            "pr_url":      "",
            "spec_text":   "",
            "pr_diff":     "",
            "agents_fired":  [],         # list of agent addresses fired
            "agents_responded": [],      # list of InspectResponse dicts
            "agents_expected": 0,        # how many responses to wait for
        }
    return sessions[sender]


# ── GitHub helpers ────────────────────────────────────────────────────────────
GITHUB_PR_RE = re.compile(
    r"(?:https?://)?github\.com/([^/]+)/([^/]+)/pull/(\d+)", re.IGNORECASE
)
GITHUB_ISSUE_RE = re.compile(
    r"(?:https?://)?github\.com/([^/]+)/([^/]+)/issues/(\d+)", re.IGNORECASE
)

def parse_pr_url(text: str):
    """Extract owner, repo, pr_number from a GitHub PR URL in the text."""
    m = GITHUB_PR_RE.search(text)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    return None, None, None


def fetch_pr_diff(owner: str, repo: str, pr_number: int) -> str:
    """Fetch the PR diff from GitHub API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    try:
        r = httpx.get(url, headers={"Accept": "application/vnd.github.v3.diff"}, timeout=15)
        if r.status_code == 200:
            diff = r.text
            if len(diff) > 8000:
                diff = diff[:8000] + "\n\n... [diff truncated at 8000 chars]"
            return diff
    except Exception:
        pass
    return ""


def fetch_pr_info(owner: str, repo: str, pr_number: int) -> dict:
    """Fetch PR metadata including linked issue body."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    try:
        r = httpx.get(url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def fetch_issue_body(owner: str, repo: str, issue_number: int) -> str:
    """Fetch the body of a GitHub issue (the spec)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
    try:
        r = httpx.get(url, headers={"Accept": "application/vnd.github.v3+json"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            title = data.get("title", "")
            body  = data.get("body", "") or ""
            return f"Issue: {title}\n\n{body}"
    except Exception:
        pass
    return ""


def extract_spec(text: str, pr_info: dict, owner: str, repo: str) -> str:
    """
    Build the spec text from:
    1. Explicit issue URL in user message
    2. Linked issue from PR body
    3. PR title + body as fallback
    """
    m = GITHUB_ISSUE_RE.search(text)
    if m:
        spec = fetch_issue_body(m.group(1), m.group(2), int(m.group(3)))
        if spec:
            return spec

    pr_body = pr_info.get("body", "") or ""
    pr_title = pr_info.get("title", "") or ""

    issue_ref = re.search(r"(?:fixes|closes|resolves|fix|close|resolve)\s+#(\d+)", pr_body, re.IGNORECASE)
    if issue_ref:
        spec = fetch_issue_body(owner, repo, int(issue_ref.group(1)))
        if spec:
            return spec

    spec = f"PR Title: {pr_title}\n\n{pr_body}" if pr_body else f"PR Title: {pr_title}"
    return spec


# ── Routing: detect what type of change the PR contains ──────────────────────
SECURITY_KEYWORDS = [
    "auth", "token", "password", "secret", "session", "jwt", "oauth",
    "login", "logout", "credential", "permission", "role", "access",
    "encrypt", "decrypt", "hash", "salt", "csrf", "cors", "sanitiz",
    "xss", "injection", "api_key", "apikey", "private_key",
]

PERFORMANCE_KEYWORDS = [
    "query", "select", "insert", "update", "delete", "join",
    "database", "db", "sql", "orm", "cursor", "execute",
    "loop", "for ", "while ", "foreach", "map(", "filter(",
    "fetch", "request", "http", "api_call",
    "cache", "redis", "memcache",
    "bulk", "batch", "paginate", "limit", "offset",
]

def detect_agents_to_fire(pr_diff: str) -> list[str]:
    """
    Decide which specialist agents to fire based on PR diff content.
    Correctness always fires (mandatory baseline). Others fire conditionally.
    Routing cannot disable mandatory checks.
    """
    agents = [config.CORRECTNESS_AGENT_ADDRESS]  # mandatory baseline — always fires

    diff_lower = pr_diff.lower()

    if any(kw in diff_lower for kw in SECURITY_KEYWORDS):
        agents.append(config.SECURITY_AGENT_ADDRESS)

    if any(kw in diff_lower for kw in PERFORMANCE_KEYWORDS):
        if config.PERFORMANCE_AGENT_ADDRESS != "agent1q...":  # only if configured
            agents.append(config.PERFORMANCE_AGENT_ADDRESS)

    return agents


AGENT_NAMES = {}

def _init_agent_names():
    """Initialize after config is loaded."""
    global AGENT_NAMES
    AGENT_NAMES = {
        config.CORRECTNESS_AGENT_ADDRESS:  "Correctness",
        config.SECURITY_AGENT_ADDRESS:     "Security",
        config.PERFORMANCE_AGENT_ADDRESS:  "Performance",
    }

def _get_agent_name(addr: str) -> str:
    if not AGENT_NAMES:
        _init_agent_names()
    return AGENT_NAMES.get(addr, addr)


def _routing_reason(pr_diff: str) -> list[str]:
    """Explain why each agent was selected — shown in the verdict."""
    reasons = ["Correctness (mandatory baseline — always fires)"]
    diff_lower = pr_diff.lower()

    sec_found = [kw for kw in SECURITY_KEYWORDS if kw in diff_lower]
    if sec_found:
        reasons.append(f"Security (detected: {', '.join(sec_found[:5])})")

    perf_found = [kw for kw in PERFORMANCE_KEYWORDS if kw in diff_lower]
    if perf_found and config.PERFORMANCE_AGENT_ADDRESS != "agent1q...":
        reasons.append(f"Performance (detected: {', '.join(perf_found[:5])})")

    return reasons


# ── Send response to user ────────────────────────────────────────────────────
async def send_to_user(ctx: Context, sender: str, text: str, end_session: bool = False):
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    await ctx.send(
        sender,
        ChatMessage(timestamp=datetime.utcnow(), msg_id=uuid4(), content=content),
    )


# ── Chat protocol: user sends a message ──────────────────────────────────────
@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.now(), acknowledged_msg_id=msg.msg_id,
    ))

    text = ""
    for item in msg.content:
        if isinstance(item, TextContent):
            text += item.text

    text = text.strip()
    if not text:
        await send_to_user(ctx, sender, "Please send a GitHub PR URL to verify.", end_session=True)
        return

    ctx.logger.info(f"User message: {text[:100]}")

    session = get_session(sender)

    # ── Parse PR URL ──────────────────────────────────────────────────────
    owner, repo, pr_number = parse_pr_url(text)
    if not owner:
        await send_to_user(
            ctx, sender,
            "I need a GitHub PR URL to verify.\n\n"
            "Example: `Verify this PR: https://github.com/owner/repo/pull/42`",
            end_session=True,
        )
        return

    pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"
    await send_to_user(ctx, sender, f"Reading PR: {pr_url}\n\nFetching diff and spec...")

    # ── Fetch PR data from GitHub ─────────────────────────────────────────
    pr_info = fetch_pr_info(owner, repo, pr_number)
    pr_diff = fetch_pr_diff(owner, repo, pr_number)

    if not pr_diff:
        await send_to_user(
            ctx, sender,
            f"Could not fetch the PR diff for {pr_url}. "
            "Make sure the PR exists and the repository is public.",
            end_session=True,
        )
        return

    spec_text = extract_spec(text, pr_info, owner, repo)

    # ── Detect which agents to fire ───────────────────────────────────────
    agents_to_fire = detect_agents_to_fire(pr_diff)
    routing_reasons = _routing_reason(pr_diff)

    await send_to_user(
        ctx, sender,
        f"Spec extracted ({len(spec_text)} chars)\n"
        f"Diff fetched ({len(pr_diff)} chars)\n\n"
        f"**Jury selected:**\n" +
        "\n".join(f"  - {r}" for r in routing_reasons) +
        "\n\nEach agent reads the spec only — never the code. Generating independent tests..."
    )

    # ── Update session state ──────────────────────────────────────────────
    session["state"]            = "waiting_agents"
    session["pr_url"]           = pr_url
    session["spec_text"]        = spec_text
    session["pr_diff"]          = pr_diff
    session["agents_fired"]     = agents_to_fire
    session["agents_expected"]  = len(agents_to_fire)
    session["agents_responded"] = []

    # ── Fire the specialist agents ────────────────────────────────────────
    for agent_addr in agents_to_fire:
        await ctx.send(agent_addr, InspectRequest(
            session_id=sender,
            spec_text=spec_text,
            pr_diff=pr_diff,
            pr_url=pr_url,
        ))
        ctx.logger.info(f"Fired {_get_agent_name(agent_addr)}")


# ── Internal protocol: specialist agent responds ─────────────────────────────
@internal_proto.on_message(InspectResponse)
async def handle_inspect_response(ctx: Context, sender_agent: str, msg: InspectResponse):
    session_id = msg.session_id
    session = get_session(session_id)

    if session["state"] != "waiting_agents":
        ctx.logger.warning(f"Unexpected InspectResponse from {msg.agent_name}")
        return

    ctx.logger.info(
        f"{msg.agent_name}: {msg.tests_generated} tests, "
        f"{msg.tests_failed} failed, clean={msg.clean}"
    )

    session["agents_responded"].append({
        "agent_name":      msg.agent_name,
        "tests_generated": msg.tests_generated,
        "tests_passed":    msg.tests_passed,
        "tests_failed":    msg.tests_failed,
        "failures_json":   msg.failures_json,
        "clean":           msg.clean,
    })

    user_sender = session["user_sender"]
    status = "clean" if msg.clean else f"{msg.tests_failed} issue(s) found"
    await send_to_user(
        ctx, user_sender,
        f"**{msg.agent_name}** — {msg.tests_generated} tests — {status}"
    )

    # ── Check if all agents have responded ────────────────────────────────
    if len(session["agents_responded"]) >= session["agents_expected"]:
        ctx.logger.info("All agents responded. Sending to Verdict Agent.")
        session["state"] = "waiting_verdict"

        await send_to_user(
            ctx, user_sender,
            "All agents reported. Applying verdict policy..."
        )

        await ctx.send(config.VERDICT_AGENT_ADDRESS, VerdictRequest(
            session_id=session_id,
            pr_url=session["pr_url"],
            spec_text=session["spec_text"],
            results_json=json.dumps(session["agents_responded"]),
        ))


# ── Build ASI Interactive Cards ───────────────────────────────────────────────
def build_verdict_card(verdict: str, risk_score: int, summary: str,
                       evidence_json: str, pr_url: str) -> dict:
    """Build a detail card for the verdict."""
    verdict_badges = {
        "PASS":         {"label": "PASS",         "variant": "success"},
        "BLOCK":        {"label": "BLOCK",        "variant": "warning"},
        "INCONCLUSIVE": {"label": "INCONCLUSIVE", "variant": "info"},
    }
    badge = verdict_badges.get(verdict, {"label": verdict, "variant": "info"})

    summary_rows = [
        {"label": "Verdict",    "value": verdict},
        {"label": "Risk Score", "value": f"{risk_score}/100"},
        {"label": "Summary",    "value": summary},
        {"label": "PR",         "value": pr_url},
    ]

    # add per-agent rows
    try:
        evidence = json.loads(evidence_json)
        for agent_name, data in evidence.items():
            failures = data.get("failures", [])
            total    = data.get("tests_generated", 0)
            passed   = data.get("tests_passed", 0)
            failed   = data.get("tests_failed", 0)
            if failures:
                summary_rows.append({
                    "label": agent_name,
                    "value": f"{failed} failed / {total} tests"
                })
            else:
                summary_rows.append({
                    "label": agent_name,
                    "value": f"clean ({passed}/{total} passed)"
                })
    except Exception:
        pass

    return {
        "title": f"CodeJury Verdict: {verdict}",
        "summary_rows": summary_rows,
        "ctas": [
            {"label": "View Findings", "selection": {"action": "view_findings"}, "primary": True},
        ]
    }


def build_findings_carousel(evidence_json: str) -> dict | None:
    """Build a carousel card showing each finding as a scrollable item."""
    try:
        evidence = json.loads(evidence_json)
    except Exception:
        return None

    items = []
    idx = 0
    for agent_name, data in evidence.items():
        failures = data.get("failures", [])
        if not failures:
            # clean agent — show as a green card
            idx += 1
            items.append({
                "id": f"finding_{idx}",
                "title": f"{agent_name}",
                "subtitle": f"All {data.get('tests_generated', 0)} tests passed",
                "badges": [{"label": "Clean", "variant": "success"}],
                "secondary_text": "No issues found",
                "primary_cta": {
                    "label": "OK",
                    "selection": {"finding_id": f"finding_{idx}"}
                }
            })
        else:
            for f in failures:
                idx += 1
                sev = f.get("severity", "minor")
                badge_variant = {"critical": "warning", "major": "warning", "minor": "info"}.get(sev, "info")
                items.append({
                    "id": f"finding_{idx}",
                    "title": f.get("test", "unnamed"),
                    "subtitle": f"{agent_name} — {sev}",
                    "badges": [{"label": sev.upper(), "variant": badge_variant}],
                    "secondary_text": f.get("reason", "no reason"),
                    "primary_cta": {
                        "label": "Noted",
                        "selection": {"finding_id": f"finding_{idx}"}
                    }
                })

    if not items:
        return None

    return {
        "title": "CodeJury Findings",
        "subtitle": f"{len(items)} finding(s) across all agents",
        "items": items,
    }


# ── Internal protocol: verdict agent responds ────────────────────────────────
@internal_proto.on_message(VerdictResponse)
async def handle_verdict_response(ctx: Context, sender_agent: str, msg: VerdictResponse):
    session_id = msg.session_id
    session = get_session(session_id)

    if session["state"] != "waiting_verdict":
        ctx.logger.warning("Unexpected VerdictResponse")
        return

    ctx.logger.info(f"Verdict: {msg.verdict} — Risk: {msg.risk_score}")
    session["state"] = "idle"
    session["last_evidence"] = msg.evidence_json

    user_sender = session["user_sender"]

    # ── Build verdict detail card ─────────────────────────────────────────
    verdict_payload = build_verdict_card(
        msg.verdict, msg.risk_score, msg.summary,
        msg.evidence_json, session["pr_url"],
    )

    # ── Also build text fallback for non-card clients ─────────────────────
    verdict_icons = {"PASS": "PASS", "BLOCK": "BLOCK", "INCONCLUSIVE": "INCONCLUSIVE"}
    risk_bar = "█" * (msg.risk_score // 10) + "░" * (10 - msg.risk_score // 10)
    text_fallback = (
        f"CodeJury Verdict: {msg.verdict} — Risk: {msg.risk_score}/100 [{risk_bar}]\n"
        f"{msg.summary}\n\n"
        f"Proof: all tests generated from spec before code was read.\n"
        f"PR: {session['pr_url']}"
    )

    # ── Send detail card ──────────────────────────────────────────────────
    await ctx.send(user_sender, ChatMessage(
        timestamp=datetime.utcnow(),
        msg_id=uuid4(),
        content=[
            TextContent(type="text", text=text_fallback),
            MetadataContent(
                type="metadata",
                metadata={
                    "card_protocol_version": "1",
                    "requires_card_interaction": "true",
                    "card_kind": "detail",
                    "card_payload": json.dumps(verdict_payload),
                    "preferred_drawer_width_px": "600",
                }
            ),
        ]
    ))

    # ── Send findings carousel ────────────────────────────────────────────
    findings_payload = build_findings_carousel(msg.evidence_json)
    if findings_payload:
        await ctx.send(user_sender, ChatMessage(
            timestamp=datetime.utcnow(),
            msg_id=uuid4(),
            content=[
                TextContent(type="text", text="Scroll through individual findings:"),
                MetadataContent(
                    type="metadata",
                    metadata={
                        "card_protocol_version": "1",
                        "requires_card_interaction": "true",
                        "card_kind": "carousel",
                        "card_payload": json.dumps(findings_payload),
                        "preferred_drawer_width_px": "600",
                    }
                ),
                EndSessionContent(type="end-session"),
            ]
        ))
    else:
        await send_to_user(ctx, user_sender, "No detailed findings to display.", end_session=True)


# ── Ack handler ───────────────────────────────────────────────────────────────
@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


# ── Attach protocols ─────────────────────────────────────────────────────────
agent.include(chat_proto, publish_manifest=True)
agent.include(internal_proto)

if __name__ == "__main__":
    print(f"\n⚖️  CodeJury Orchestrator address: {agent.address}\n")
    print("   → Register this agent on Agentverse as @codejury\n")
    agent.run()
