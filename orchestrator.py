"""
CodeJury Orchestrator: the only agent users talk to, via ASI:One.

Flow for "Verify https://github.com/mkg0908/codejury-demo/pull/1":
  1. Fetch the PR's head commit into the local demo repo (allowlisted repo only;
     a local branch name such as "ai-export-orders" also works). Load the spec
     from demo/spec.md, never from the PR.
  2. Select the jury from the diff. Correctness always runs; routing can add
     Security and Performance but never remove mandatory checks.
  3. Send each specialist an InspectRequest and run the repository's own tests
     (baseline) in the sandbox at the same time.
  4. Wait for every specialist, up to AGENT_TIMEOUT_S. A missing or silent
     specialist becomes an "error" check, so the verdict is INCONCLUSIVE, never PASS.
  5. Send all checks to the Verdict Agent (deterministic policy gate). If it does
     not answer within VERDICT_TIMEOUT_S, apply the same gate locally.
  6. Reply with the verdict, the evidence and ASI interactive cards.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, TextContent, EndSessionContent,
    MetadataContent, chat_protocol_spec,
)

import config
import jury
from models import InspectRequest, InspectResponse, VerdictRequest, VerdictResponse

AGENT_TIMEOUT_S = 300
VERDICT_TIMEOUT_S = 60
UNSET_ADDRESS = "agent1q..."

agent = Agent(
    name="codejury-orchestrator",
    seed=config.require_env("ORCHESTRATOR_SEED"),
    port=8001,
    mailbox=True,
    publish_agent_details=True,
)

chat_proto     = Protocol(spec=chat_protocol_spec)
internal_proto = Protocol(name="codejury-internal", version="1.0.0")

SPECIALIST_ADDRESSES = {
    "correctness": config.CORRECTNESS_AGENT_ADDRESS,
    "security":    config.SECURITY_AGENT_ADDRESS,
    "performance": config.PERFORMANCE_AGENT_ADDRESS,
}

# run_id -> session dict. A session is one verification run.
sessions: dict[str, dict] = {}
# user address -> evidence of their latest verdict, for the "View Full Report" button.
last_reports: dict[str, dict] = {}


# ── Messaging helpers ────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


async def send_to_user(ctx: Context, user: str, text: str, end_session: bool = False):
    content = [TextContent(type="text", text=text)]
    if end_session:
        content.append(EndSessionContent(type="end-session"))
    await ctx.send(user, ChatMessage(timestamp=_now(), msg_id=uuid4(), content=content))


def _help_text(refs: list[str]) -> str:
    listed = "\n".join(f"  - `{r}`" for r in refs if not r.startswith("pr-")) \
        or "  (none: run `python demo/build_demo_repo.py`)"
    return (
        "Send me a pull request to verify, for example:\n"
        f"`Verify https://github.com/{jury.GITHUB_DEMO_REPO}/pull/1`\n\n"
        f"Only PRs from `{jury.GITHUB_DEMO_REPO}` are supported. You can also name a "
        f"local demo branch:\n{listed}"
    )


CARD_PHRASES = ("view full report", "full report", "see agent reports", "view findings",
                "see_reports", "view_findings", "full_report", "noted")


def _card_action(text: str) -> dict | None:
    """Detect a card button click. ASI:One sends it back as a JSON chat message,
    or its planner sometimes rephrases it as prose ("View Full Report")."""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and {"action", "finding_id", "defect", "agent", "id"} & data.keys():
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    lower = text.lower()
    if any(p in lower for p in CARD_PHRASES):
        return {"action": "full_report" if "full report" in lower or "full_report" in lower else "other"}
    return None


# ── Chat protocol: user sends a message ──────────────────────────────────────
@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=_now(), acknowledged_msg_id=msg.msg_id))

    text = "".join(item.text for item in msg.content if isinstance(item, TextContent)).strip()
    ctx.logger.info(f"User message: {text[:100]}")

    # Card button clicks are not new verification requests. A PR link always is.
    action = None if jury.parse_pr_url(text) else _card_action(text)
    if action is not None:
        if action.get("action") == "full_report":
            evidence = last_reports.get(sender)
            carousel = build_report_carousel(evidence) if evidence else None
            if carousel:
                await ctx.send(sender, ChatMessage(timestamp=_now(), msg_id=uuid4(), content=[
                    TextContent(type="text", text="Full verification report:"),
                    _card("carousel", carousel),
                    EndSessionContent(type="end-session"),
                ]))
            else:
                await send_to_user(ctx, sender, "No report yet. Send a PR link to verify first.", end_session=True)
        else:
            await send_to_user(ctx, sender, "Send another PR link to verify a new change.", end_session=True)
        return

    if any(s["user"] == sender and s["state"] != "done" for s in sessions.values()):
        await send_to_user(ctx, sender, "A verification for you is already running. Please wait for its verdict.")
        return

    # A PR link wins; otherwise look for a local demo branch name.
    pr = None
    pr_parts = jury.parse_pr_url(text)
    if pr_parts:
        await send_to_user(ctx, sender, f"Fetching PR #{pr_parts[2]} from GitHub...")
        try:
            pr = await asyncio.to_thread(jury.fetch_pr, *pr_parts)
        except jury.PRError as e:
            await send_to_user(ctx, sender, f"{e}\n\n{_help_text(jury.list_refs())}", end_session=True)
            return
        ref = pr["ref"]
    else:
        refs = jury.list_refs()
        ref = jury.find_ref(text, [r for r in refs if not r.startswith("pr-")])
        if not ref:
            await send_to_user(ctx, sender, _help_text(refs), end_session=True)
            return

    run_id = jury.new_run_id()
    spec = jury.load_spec()
    diff = jury.get_diff(jury.DEMO_REPO, ref)
    jury_selection = jury.select_specialists(diff)
    routing = [{"category": c, "reason": r} for c, r in jury_selection]
    label = (f"PR #{pr['number']} \"{pr['title']}\" ({pr['head_sha'][:7]}) {pr['url']}" if pr
             else f"demo repo: {jury.BASE_REF}...{ref}")

    session = {
        "run_id": run_id, "user": sender, "ref": ref, "label": label,
        "state": "waiting_agents", "deadline": time.monotonic() + AGENT_TIMEOUT_S,
        "routing": routing, "expected": [c for c, _ in jury_selection],
        "responses": {}, "checks": [], "baseline_done": False,
    }
    sessions[run_id] = session
    jury.save_run(run_id, "request.json", {
        "run_id": run_id, "ref": ref, "base": jury.BASE_REF, "pr": pr, "routing": routing,
        "spec": spec, "diff": diff, "started_at": _now().isoformat(),
    })

    await send_to_user(
        ctx, sender,
        f"Verifying {label} against `{jury.BASE_REF}` (run `{run_id}`)\n"
        "Requirements come from CodeJury's own spec, not from the PR.\n"
        f"Diff: {len(diff)} chars. Spec: {len(spec)} chars.\n\n"
        "**Jury selected:**\n" +
        "\n".join(f"  - {jury.DISPLAY_NAMES[c]}: {r}" for c, r in jury_selection) +
        "\n\nEach specialist writes tests from the spec only, never the code, and runs "
        "them in an isolated sandbox. Running the repository's own tests now..."
    )

    # Fire specialists. An unconfigured address is an incomplete check, not a skip.
    for category in session["expected"]:
        address = SPECIALIST_ADDRESSES[category]
        if not address or address == UNSET_ADDRESS:
            _record_response(session, category, [jury.missing_agent_check(
                category, f"{jury.DISPLAY_NAMES[category]} agent address is not configured")], [])
            continue
        await ctx.send(address, InspectRequest(
            session_id=run_id, run_id=run_id, ref=ref,
            spec_text=spec, pr_diff=diff, pr_url=session["label"],
        ))
        ctx.logger.info(f"Fired {category} for run {run_id}")

    # Baseline runs here while the specialists work.
    checks, run = await asyncio.to_thread(jury.baseline_checks, jury.DEMO_REPO, ref)
    session["checks"] += checks
    session["baseline_done"] = True
    passed = sum(c["status"] == "passed" for c in checks)
    status = (f"{passed}/{len(checks)} passed" if run["status"] == "completed"
              else f"did not complete ({run.get('error')})")
    await send_to_user(ctx, sender, f"**Baseline** (repository's own tests): {status}")
    await _maybe_request_verdict(ctx, session)


def _record_response(session: dict, category: str, checks: list[dict], notes: list[str]):
    session["responses"][category] = {"checks": checks, "notes": notes}
    session["checks"] += checks


# ── Internal protocol: specialist agent responds ─────────────────────────────
@internal_proto.on_message(InspectResponse)
async def handle_inspect_response(ctx: Context, sender: str, msg: InspectResponse):
    session = sessions.get(msg.run_id)
    if not session or session["state"] != "waiting_agents":
        ctx.logger.warning(f"Ignoring late or unknown response from {msg.agent_name} for {msg.run_id}")
        return
    if msg.category not in session["expected"] or msg.category in session["responses"]:
        ctx.logger.warning(f"Ignoring unexpected response from {msg.agent_name}")
        return
    if sender != SPECIALIST_ADDRESSES.get(msg.category):
        ctx.logger.warning(f"Ignoring {msg.category} response from unexpected sender {sender}")
        return

    try:
        checks = json.loads(msg.checks_json)
        notes = json.loads(msg.notes_json)
    except json.JSONDecodeError:
        checks, notes = [jury.missing_agent_check(msg.category, "Agent sent malformed results")], []
    for c in checks:  # an agent can only report on its own category
        c["category"] = msg.category
    _record_response(session, msg.category, checks, notes)

    if msg.error:
        status = f"could not complete ({msg.error})"
    elif msg.tests_failed:
        status = f"{msg.tests_failed} of {msg.tests_run} tests failed"
    else:
        status = f"{msg.tests_passed}/{msg.tests_run} tests passed"
    extra = f"\n  Note: {notes[0]}" if notes else ""
    await send_to_user(ctx, session["user"], f"**{msg.agent_name}**: {status}{extra}")
    await _maybe_request_verdict(ctx, session)


async def _maybe_request_verdict(ctx: Context, session: dict):
    if session["state"] != "waiting_agents" or not session["baseline_done"]:
        return
    if any(c not in session["responses"] for c in session["expected"]):
        return

    session["state"] = "waiting_verdict"
    session["deadline"] = time.monotonic() + VERDICT_TIMEOUT_S
    if config.VERDICT_AGENT_ADDRESS == UNSET_ADDRESS:
        await _finish_locally(ctx, session, "Verdict agent address is not configured")
        return
    await send_to_user(ctx, session["user"], "All checks reported. Applying the verdict policy...")
    await ctx.send(config.VERDICT_AGENT_ADDRESS, VerdictRequest(
        session_id=session["run_id"], run_id=session["run_id"], ref=session["ref"],
        pr_url=session["label"], checks_json=json.dumps(session["checks"]),
        routing_json=json.dumps(session["routing"]),
    ))


async def _finish_locally(ctx: Context, session: dict, why: str):
    """Same deterministic gate, applied here when the Verdict agent is unavailable."""
    ctx.logger.warning(f"{why}; applying policy gate locally for {session['run_id']}")
    record = await asyncio.to_thread(
        jury.decide, session["run_id"], session["ref"], session["checks"], session["routing"], False)
    await _deliver(ctx, session, record["gate"]["verdict"], record["summary"] + f" ({why}.)",
                   record["gate"]["reasons"], record["evidence"])


# ── Timeouts ─────────────────────────────────────────────────────────────────
@agent.on_interval(period=5.0)
async def check_timeouts(ctx: Context):
    for session in list(sessions.values()):
        if session["state"] == "done" or time.monotonic() < session["deadline"]:
            continue
        if session["state"] == "waiting_agents":
            for category in session["expected"]:
                if category not in session["responses"]:
                    _record_response(session, category, [jury.missing_agent_check(
                        category, f"{jury.DISPLAY_NAMES[category]} agent did not respond within {AGENT_TIMEOUT_S}s")], [])
                    await send_to_user(ctx, session["user"],
                                       f"**{jury.DISPLAY_NAMES[category]}**: no response, counted as incomplete")
            await _maybe_request_verdict(ctx, session)
        elif session["state"] == "waiting_verdict":
            await _finish_locally(ctx, session, f"Verdict agent did not respond within {VERDICT_TIMEOUT_S}s")


# ── Internal protocol: verdict agent responds ────────────────────────────────
@internal_proto.on_message(VerdictResponse)
async def handle_verdict_response(ctx: Context, sender: str, msg: VerdictResponse):
    session = sessions.get(msg.run_id)
    if not session or session["state"] != "waiting_verdict" or sender != config.VERDICT_AGENT_ADDRESS:
        ctx.logger.warning(f"Ignoring unexpected VerdictResponse for {msg.run_id}")
        return
    try:
        reasons = json.loads(msg.reasons_json)
        evidence = json.loads(msg.evidence_json)
    except json.JSONDecodeError:
        await _finish_locally(ctx, session, "Verdict agent sent malformed results")
        return
    await _deliver(ctx, session, msg.verdict, msg.summary, reasons, evidence)


# ── Output: text + ASI interactive cards ─────────────────────────────────────
VERDICT_ICONS = {"PASS": "🟢 PASS", "BLOCK": "🔴 BLOCK", "INCONCLUSIVE": "🟡 INCONCLUSIVE"}


def build_verdict_text(session: dict, verdict: str, summary: str, reasons: list, evidence: dict) -> str:
    lines = [f"## ⚖️ CodeJury verdict: {VERDICT_ICONS.get(verdict, verdict)}", "", summary, "",
             "**Policy:** " + "; ".join(reasons), ""]
    for name, data in evidence.items():
        failed = [f for f in data["failures"] if f["severity"] != "advisory"]
        head = f"**{name}**: {data['tests_passed']}/{data['tests_generated']} passed"
        lines.append(head if failed else head + " ✅")
        for f in data["failures"]:
            icon = {"blocking": "❌", "incomplete": "⚠️", "advisory": "ℹ️"}.get(f["severity"], "•")
            lines.append(f"  {icon} `{f['test']}` ({f['source']}): {f['reason']}")
    lines += ["", f"Change: {session['label']}", f"Run: `{session['run_id']}` (tests and logs saved under runs/)",
              "Tests were written from the spec before any implementation was read."]
    return "\n".join(lines)


def _shorten_reason(reason: str) -> str:
    """First sentence or 120 characters of a finding, for the summary card."""
    first = (reason or "Issue detected").splitlines()[0]
    for sep in (". ", "; "):
        if sep in first:
            return first[:first.index(sep) + 1].strip()
    return first[:120].strip() + ("..." if len(first) > 120 else "")


def build_summary_custom_card(session: dict, verdict: str, summary: str, reasons: list,
                              evidence: dict) -> dict:
    """Card 1: verdict, plain-English blocking and incomplete findings, and a
    "View Full Report" button that opens the full report carousel."""
    total = sum(d["tests_generated"] for d in evidence.values())
    passed = sum(d["tests_passed"] for d in evidence.values())
    blocking = [(n, f) for n, d in evidence.items() for f in d["failures"] if f["severity"] == "blocking"]
    incomplete = [(n, f) for n, d in evidence.items() for f in d["failures"] if f["severity"] == "incomplete"]

    children = [
        {"type": "text", "value": "; ".join(reasons), "style": "emphasis"},
        {"type": "text", "value": f"{passed} of {total} tests passed. Every test ran in the sandbox.",
         "style": "muted"},
        {"type": "divider"},
    ]
    for title, items, label, variant in (("Blocking issues", blocking, "BLOCKING", "warning"),
                                         ("Could not complete", incomplete, "INCOMPLETE", "info")):
        if items:
            children.append({"type": "heading", "value": title, "level": 3})
            for name, f in items[:6]:
                children.append({"type": "group", "direction": "row", "gap": 4, "children": [
                    {"type": "badge", "label": label, "variant": variant},
                    {"type": "text", "value": f"{name}: {_shorten_reason(f['reason'])}", "style": "body"},
                ]})
    if not blocking and not incomplete:
        children.append({"type": "text", "value": "No blocking or incomplete checks.", "style": "body"})
    children += [
        {"type": "divider"},
        {"type": "text", "value": summary, "style": "muted"},
        {"type": "text", "value": f"{session['label']} · run {session['run_id']}", "style": "muted"},
        {"type": "button", "label": "View Full Report", "primary": True,
         "action": {"selection": {"action": "full_report"}}},
    ]
    return {"root": {"type": "section", "title": f"CodeJury: {verdict}", "children": children}}


def build_report_carousel(evidence: dict) -> dict | None:
    """Card 2, sent when the user clicks View Full Report: one item per finding,
    plus one item per check group that ran tests and found nothing.
    A group with 0 tests is never shown as clean."""
    items, idx, defects, total = [], 0, 0, 0
    for name, data in evidence.items():
        total += data["tests_generated"]
        for f in data["failures"]:
            idx += 1
            defects += f["severity"] == "blocking"
            variant = {"blocking": "warning", "incomplete": "info", "advisory": "info"}.get(f["severity"], "info")
            items.append({
                "id": f"defect_{idx}", "title": f["test"],
                "subtitle": f"Found by {name} ({f['source']} test)",
                "badges": [{"label": f["severity"].upper(), "variant": variant}],
                "secondary_text": f["reason"],
                "primary_cta": {"label": "Details", "selection": {"id": f"defect_{idx}"}},
            })
        if not data["failures"] and data["tests_generated"]:
            idx += 1
            items.append({
                "id": f"agent_{idx}", "title": name,
                "subtitle": f"{data['tests_passed']} of {data['tests_generated']} tests passed",
                "badges": [{"label": "ALL CLEAR", "variant": "success"}],
                "secondary_text": f"No defects found across {data['tests_generated']} executed tests",
                "primary_cta": {"label": "OK", "selection": {"id": f"agent_{idx}"}},
            })
    if not items:
        return None
    subtitle = (f"{defects} defect(s) across {total} tests. Scroll for details." if defects
                else f"All {total} tests passed. No defects found.")
    return {"title": "Full Verification Report", "subtitle": subtitle, "items": items}


def _card(kind: str, payload: dict, width: str = "600") -> MetadataContent:
    return MetadataContent(type="metadata", metadata={
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": kind,
        "card_payload": json.dumps(payload),
        "preferred_drawer_width_px": width,
    })


async def _deliver(ctx: Context, session: dict, verdict: str, summary: str, reasons: list, evidence: dict):
    session["state"] = "done"
    ctx.logger.info(f"Run {session['run_id']}: {verdict}")
    user = session["user"]
    last_reports[user] = evidence  # for the "View Full Report" button

    # Full evidence as text (shown by clients without card support) + summary card.
    await ctx.send(user, ChatMessage(timestamp=_now(), msg_id=uuid4(), content=[
        TextContent(type="text", text=build_verdict_text(session, verdict, summary, reasons, evidence)),
        _card("custom", build_summary_custom_card(session, verdict, summary, reasons, evidence), "540"),
        EndSessionContent(type="end-session"),
    ]))


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(chat_proto, publish_manifest=True)
agent.include(internal_proto)

if __name__ == "__main__":
    print(f"\n⚖️  CodeJury Orchestrator address: {agent.address}\n")
    print("   → Register this agent on Agentverse as @codejury\n")
    agent.run()
