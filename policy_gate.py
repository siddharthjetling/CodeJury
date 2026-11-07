"""Deterministic policy gate: turns check results into PASS / BLOCK / INCONCLUSIVE.

No LLM is involved. The same inputs and policy always give the same verdict.

Policy (policy.json):
  mandatory_categories  must have run and completed, whatever the router decided
  blocking_categories   a failed check in these categories blocks the merge
  advisory_categories   failures are reported but never block

Rules, in order:
  1. Any failed check in a blocking category             -> BLOCK
  2. Any blocking-category check that errored/timed out,
     a mandatory category with no checks, or no checks   -> INCONCLUSIVE
  3. Otherwise                                           -> PASS

BLOCK wins over INCONCLUSIVE: if there is proof of a violation, the merge is
blocked even when other checks could not run (they are still listed).

A check is a dict:
  {"id": str, "category": str, "status": "passed"|"failed"|"error"|"timeout",
   "message": str, "evidence": dict}

Use checks_from_run() to convert a sandbox.runner result into checks.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parent / "policy.json"
CHECK_STATUSES = {"passed", "failed", "error", "timeout"}


def load_policy(path: Path = POLICY_PATH) -> dict:
    raw = Path(path).read_bytes()
    policy = json.loads(raw)
    policy["sha256"] = hashlib.sha256(raw).hexdigest()
    return policy


def checks_from_run(run: dict, category: str) -> list[dict]:
    """Convert one sandbox.runner result into gate checks for a category."""
    evidence_base = {"suite": run.get("suite"), "ref": run.get("ref"), "commit": run.get("commit")}
    if run.get("status") != "completed":
        return [{
            "id": f"{category}:{run.get('suite')}:run",
            "category": category,
            "status": "timeout" if run.get("status") == "timeout" else "error",
            "message": run.get("error") or "Test run did not complete",
            "evidence": {**evidence_base, "log_tail": run.get("log_tail", "")},
        }]
    checks = []
    for t in run["tests"]:
        if t["outcome"] == "skipped":
            continue
        checks.append({
            "id": f"{category}:{t['id']}",
            "category": category,
            "status": {"passed": "passed", "failed": "failed"}.get(t["outcome"], "error"),
            "message": t.get("message", ""),
            "evidence": {**evidence_base, "test": t["id"], "duration_s": t.get("duration_s")},
        })
    if not checks:
        checks.append({
            "id": f"{category}:{run.get('suite')}:run",
            "category": category,
            "status": "error",
            "message": "All tests were skipped",
            "evidence": evidence_base,
        })
    return checks


def evaluate(checks: list[dict], policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    mandatory = set(policy["mandatory_categories"])
    blocking_cats = set(policy["blocking_categories"])
    advisory_cats = set(policy.get("advisory_categories", []))

    blocking, inconclusive, advisory, passed = [], [], [], []
    for c in checks:
        status, cat = c.get("status"), c.get("category")
        if status not in CHECK_STATUSES:
            inconclusive.append({**c, "reason": f"unknown status {status!r}"})
        elif cat in advisory_cats or cat not in blocking_cats:
            if status != "passed":
                advisory.append(c)
            else:
                passed.append(c)
        elif status == "failed":
            blocking.append(c)
        elif status in ("error", "timeout"):
            inconclusive.append(c)
        else:
            passed.append(c)

    ran = {c.get("category") for c in checks}
    missing = sorted(mandatory - ran)

    reasons = []
    if blocking:
        verdict = "BLOCK"
        reasons.append(f"{len(blocking)} required check(s) failed")
    elif inconclusive or missing or not checks:
        verdict = "INCONCLUSIVE"
        if inconclusive:
            reasons.append(f"{len(inconclusive)} required check(s) did not complete")
        if missing:
            reasons.append("mandatory categories with no checks: " + ", ".join(missing))
        if not checks:
            reasons.append("no checks were run")
    else:
        verdict = "PASS"
        reasons.append(f"all {len(passed)} required check(s) passed")
    if verdict == "BLOCK" and (inconclusive or missing):
        reasons.append(f"also {len(inconclusive)} incomplete check(s), missing mandatory: {missing or 'none'}")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "blocking": blocking,
        "inconclusive": inconclusive,
        "missing_mandatory": missing,
        "advisory": advisory,
        "passed_count": len(passed),
        "policy_version": policy.get("version"),
        "policy_sha256": policy.get("sha256"),
    }
