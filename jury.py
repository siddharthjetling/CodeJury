"""Shared jury logic used by the orchestrator, the verdict agent and verify.py.

Everything here is plain Python with no uAgents dependency, so the whole
verification flow can also run in-process (see verify.py).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import policy_gate
from sandbox import runner

ROOT = Path(__file__).resolve().parent
DEMO_REPO = ROOT / ".demo_repo"
SPEC_PATH = ROOT / "demo" / "spec.md"
RUNS_DIR = ROOT / "runs"
BASE_REF = "main"

DISPLAY_NAMES = {
    "baseline": "Baseline",
    "correctness": "Correctness",
    "security": "Security",
    "performance": "Performance",
}


# ── Inputs: which change, which spec ─────────────────────────────────────────
def list_refs(repo: Path = DEMO_REPO) -> list[str]:
    r = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        capture_output=True, text=True,
    )
    return [b for b in r.stdout.split() if b != BASE_REF] if r.returncode == 0 else []


def find_ref(text: str, refs: list[str]) -> str | None:
    """Pick the branch named in the message. Longest match wins, so
    'ai-export-orders-fixed' is not mistaken for 'ai-export-orders'."""
    words = set(re.findall(r"[\w./-]+", text))
    matches = [r for r in refs if r in words]
    return max(matches, key=len) if matches else None


def load_spec(path: Path = SPEC_PATH) -> str:
    return path.read_text(encoding="utf-8")


def get_diff(repo: Path, ref: str, base: str = BASE_REF) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "diff", f"{base}...{ref}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout if r.returncode == 0 else ""


# ── GitHub pull requests (allowlisted demo repo only) ────────────────────────
# Only PRs from this repo are verified: CodeJury runs the PR's code, so it must
# be code we know how to set up. The spec always comes from demo/spec.md, never
# from the PR, so a PR author can't weaken the requirements.
GITHUB_DEMO_REPO = os.environ.get("CODEJURY_GITHUB_REPO", "mkg0908/codejury-demo")
GITHUB_PR_RE = re.compile(r"(?:https?://)?github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)", re.IGNORECASE)


class PRError(RuntimeError):
    pass


def parse_pr_url(text: str) -> tuple[str, str, int] | None:
    m = GITHUB_PR_RE.search(text)
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_pr(owner: str, repo: str, number: int, local_repo: Path = DEMO_REPO) -> dict:
    """Fetch an allowlisted PR's head commit into the local demo repo as branch pr-<n>.

    Returns {ref, number, title, url, head_sha, base_ref}. Raises PRError.
    """
    import httpx

    full_name = f"{owner}/{repo}"
    if full_name.lower() != GITHUB_DEMO_REPO.lower():
        raise PRError(f"I can only verify pull requests from {GITHUB_DEMO_REPO}.")
    if not (local_repo / ".git").exists():
        raise PRError("The local demo repo is missing. Run: python demo/build_demo_repo.py")

    try:
        r = httpx.get(f"https://api.github.com/repos/{full_name}/pulls/{number}",
                      headers=_github_headers(), timeout=15)
    except httpx.HTTPError as e:
        raise PRError(f"Could not reach GitHub: {e}") from e
    if r.status_code != 200:
        try:
            reason = r.json().get("message", "")
        except ValueError:
            reason = r.text[:200]
        hint = " Set GITHUB_TOKEN in .env to avoid rate limits." if r.status_code in (403, 429) else ""
        raise PRError(f"GitHub returned {r.status_code} for PR #{number}: {reason}.{hint}")
    pr = r.json()
    head_sha, base_ref = pr["head"]["sha"], pr["base"]["ref"]
    if base_ref != BASE_REF:
        raise PRError(f"PR #{number} targets {base_ref!r}; only PRs into {BASE_REF!r} are supported.")

    ref = f"pr-{number}"
    fetch = subprocess.run(
        ["git", "-C", str(local_repo), "fetch", "--no-tags", "--force",
         f"https://github.com/{full_name}.git", f"pull/{number}/head:refs/heads/{ref}"],
        capture_output=True, text=True,
    )
    if fetch.returncode != 0:
        raise PRError(f"git fetch failed: {fetch.stderr.strip()[-300:]}")
    local_sha = subprocess.run(["git", "-C", str(local_repo), "rev-parse", ref],
                               capture_output=True, text=True).stdout.strip()
    if local_sha != head_sha:
        raise PRError(f"PR #{number} changed while fetching ({local_sha[:7]} != {head_sha[:7]}). Try again.")

    return {"ref": ref, "number": number, "title": pr.get("title", ""),
            "url": pr.get("html_url", ""), "head_sha": head_sha, "base_ref": base_ref}


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


# ── Routing ──────────────────────────────────────────────────────────────────
SECURITY_KEYWORDS = [
    "auth", "token", "password", "secret", "session", "jwt", "oauth",
    "login", "logout", "credential", "permission", "role", "access",
    "encrypt", "decrypt", "hash", "salt", "csrf", "cors", "sanitiz",
    "xss", "injection", "api_key", "apikey", "private_key", "customer_id", "user_id",
]

PERFORMANCE_KEYWORDS = [
    "query", "select", "insert", "update", "delete", "join",
    "database", "sql", "orm", "cursor", "execute",
    "for ", "while ", "fetch", "cache", "bulk", "batch", "paginate", "limit", "offset",
]


def changed_lines(diff: str) -> str:
    """Only added/removed lines, so unchanged context doesn't trigger routing."""
    return "\n".join(
        line[1:] for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ).lower()


def select_specialists(diff: str) -> list[tuple[str, str]]:
    """Return [(category, reason)]. Correctness is mandatory and always included;
    routing can add specialists but never remove mandatory checks."""
    changed = changed_lines(diff)
    jury = [("correctness", "mandatory baseline, always runs")]
    sec = [k for k in SECURITY_KEYWORDS if k in changed]
    if sec:
        jury.append(("security", "changed code mentions " + ", ".join(sec[:5])))
    perf = [k.strip() for k in PERFORMANCE_KEYWORDS if k in changed]
    if perf:
        jury.append(("performance", "changed code mentions " + ", ".join(perf[:5])))
    return jury


# ── Baseline ─────────────────────────────────────────────────────────────────
def baseline_checks(repo: Path, ref: str) -> tuple[list[dict], dict]:
    run = runner.run_baseline(repo, ref)
    checks = policy_gate.checks_from_run(run, "baseline")
    for c in checks:
        c["source"] = "repository"
    return checks, run


def missing_agent_check(category: str, reason: str) -> dict:
    return {
        "id": f"{category}:agent",
        "category": category,
        "source": "agent",
        "status": "error",
        "message": reason,
        "evidence": {},
    }


# ── Evidence and summary ─────────────────────────────────────────────────────
def build_evidence(checks: list[dict], gate: dict) -> dict:
    """Group checks per specialist for display:
    {name: {tests_generated, tests_passed, tests_failed, failures: [{test, reason, severity}]}}
    severity is "blocking", "incomplete" or "advisory"."""
    blocking = {c["id"] for c in gate["blocking"]}
    incomplete = {c["id"] for c in gate["inconclusive"]}
    advisory = {c["id"] for c in gate["advisory"]}

    evidence: dict[str, dict] = {}
    for c in checks:
        name = DISPLAY_NAMES.get(c["category"], c["category"].title())
        e = evidence.setdefault(name, {"tests_generated": 0, "tests_passed": 0,
                                       "tests_failed": 0, "failures": []})
        e["tests_generated"] += 1
        if c["status"] == "passed":
            e["tests_passed"] += 1
            continue
        if c["status"] == "failed":
            e["tests_failed"] += 1
        severity = ("blocking" if c["id"] in blocking else
                    "incomplete" if c["id"] in incomplete else
                    "advisory" if c["id"] in advisory else "incomplete")
        lines = [ln for ln in (c.get("message") or "").splitlines() if ln.strip()]
        e["failures"].append({
            "test": c["id"].split("::")[-1],
            "check_id": c["id"],
            "source": c.get("source", ""),
            "reason": lines[0][:300] if lines else c["status"],
            "severity": severity,
        })
    return evidence


SUMMARY_SYSTEM = """You write a one-sentence summary of a code verification verdict.

The verdict is already decided by a fixed policy. Do not change it, question it,
or add findings that are not in the input. Mention the most serious finding
by what it means for users. Return only the sentence."""


def fallback_summary(gate: dict) -> str:
    v = gate["verdict"]
    if v == "PASS":
        return f"All {gate['passed_count']} required checks passed."
    if v == "BLOCK":
        first = gate["blocking"][0]
        msg = (first.get("message") or "").splitlines()
        return f"{len(gate['blocking'])} required check(s) failed, e.g. {first['id']}: {msg[0] if msg else 'failed'}"
    return "Required verification could not complete: " + "; ".join(gate["reasons"])


def summarize(gate: dict, evidence: dict) -> str:
    """LLM-written summary. Falls back to a fixed sentence if the LLM fails.
    The LLM never sees instructions from the candidate code, only check results."""
    try:
        import llm

        text = llm.chat(
            SUMMARY_SYSTEM,
            f"Verdict: {gate['verdict']}\nReasons: {gate['reasons']}\n"
            f"Evidence: {json.dumps(evidence)[:4000]}",
            max_tokens=200,
        ).strip()
        return text or fallback_summary(gate)
    except Exception:
        return fallback_summary(gate)


def save_run(run_id: str, name: str, record: dict) -> Path:
    path = RUNS_DIR / run_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


def decide(run_id: str, ref: str, checks: list[dict], routing: list[dict],
           with_summary: bool = True) -> dict:
    """Policy gate + evidence + summary, saved to runs/<run_id>/verdict.json."""
    gate = policy_gate.evaluate(checks)
    evidence = build_evidence(checks, gate)
    summary = summarize(gate, evidence) if with_summary else fallback_summary(gate)
    record = {"run_id": run_id, "ref": ref, "routing": routing, "gate": gate,
              "evidence": evidence, "summary": summary}
    save_run(run_id, "verdict.json", record)
    return record
