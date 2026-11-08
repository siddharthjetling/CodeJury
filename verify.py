"""Run the full CodeJury flow in one process, without uAgents.

Same steps as the agents: routing, baseline, each specialist's protected +
spec-first generated tests in the sandbox, the policy gate, and the summary.
Useful for debugging and as a fallback for the live demo.

Usage:
  python verify.py https://github.com/mkg0908/codejury-demo/pull/1
  python verify.py ai-export-orders            # uses the Meta API to write tests
  python verify.py ai-export-orders --no-llm   # protected tests only, no API key needed
"""
import argparse
import json
import sys

import config  # noqa: F401  (loads .env)
import jury
import specialist


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ref")
    p.add_argument("--no-llm", action="store_true", help="skip LLM test generation and summary")
    p.add_argument("--json", action="store_true", help="print the full verdict record")
    a = p.parse_args()

    pr_parts = jury.parse_pr_url(a.ref)
    if pr_parts:
        try:
            pr = jury.fetch_pr(*pr_parts)
        except jury.PRError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"PR #{pr['number']} \"{pr['title']}\" -> {pr['ref']} ({pr['head_sha'][:7]})")
        a.ref = pr["ref"]

    refs = jury.list_refs()
    if a.ref not in refs:
        print(f"Unknown change {a.ref!r}. Available: {', '.join(refs) or 'none'}", file=sys.stderr)
        return 2

    run_id = jury.new_run_id()
    spec = jury.load_spec()
    diff = jury.get_diff(jury.DEMO_REPO, a.ref)
    selection = jury.select_specialists(diff)
    routing = [{"category": c, "reason": r} for c, r in selection]
    jury.save_run(run_id, "request.json", {"run_id": run_id, "ref": a.ref, "base": jury.BASE_REF,
                                           "routing": routing, "spec": spec, "diff": diff})

    print(f"Run {run_id}: {jury.BASE_REF}...{a.ref}")
    for c, r in selection:
        print(f"  jury: {jury.DISPLAY_NAMES[c]} ({r})")

    checks, _ = jury.baseline_checks(jury.DEMO_REPO, a.ref)
    for category, _ in selection:
        res = specialist.investigate(category, spec, a.ref, run_id, use_llm=not a.no_llm)
        checks += res["checks"]
        for note in res["notes"]:
            print(f"  note ({category}): {note}")
        for d in res["discarded"]:
            print(f"  discarded ({category}): {d['test']}: {d['reason']}")

    record = jury.decide(run_id, a.ref, checks, routing, with_summary=not a.no_llm)
    if a.json:
        print(json.dumps(record, indent=2))
    else:
        icon = {"passed": "PASS", "failed": "FAIL", "error": "ERR ", "timeout": "TIME"}
        print()
        for c in checks:
            print(f"  [{icon[c['status']]}] ({c.get('source', '')}) {c['id']}")
        gate = record["gate"]
        print(f"\nVerdict: {gate['verdict']} ({'; '.join(gate['reasons'])})")
        print(f"Summary: {record['summary']}")
        for c in gate["blocking"] + gate["inconclusive"]:
            first = (c.get("message") or "").splitlines()[:1]
            print(f"  - {c['id']}: {first[0] if first else ''}")
        print(f"\nArtifacts: runs/{run_id}/")
    return {"PASS": 0, "BLOCK": 1}.get(record["gate"]["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
