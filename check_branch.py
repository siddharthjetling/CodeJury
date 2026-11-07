"""Run baseline + protected verifier tests on a demo branch and print the verdict.

No LLM involved. This is the deterministic backbone that the agents build on,
and the fallback for the live demo.

Usage:
  python check_branch.py ai-export-orders
  python check_branch.py ai-export-orders-fixed --json
"""
import argparse
import json
import sys
from pathlib import Path

import policy_gate
from sandbox import runner

PROTECTED = Path(__file__).resolve().parent / "verifier" / "protected"
CATEGORIES = ["correctness", "security", "performance"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ref")
    p.add_argument("--repo", type=Path, default=runner.DEFAULT_REPO)
    p.add_argument("--json", action="store_true", help="print full gate output as JSON")
    a = p.parse_args()

    checks = policy_gate.checks_from_run(runner.run_baseline(a.repo, a.ref), "baseline")
    for cat in CATEGORIES:
        files = sorted((PROTECTED / cat).glob("test_*.py"))
        checks += policy_gate.checks_from_run(runner.run_verifier(a.repo, a.ref, files), cat)

    result = policy_gate.evaluate(checks)
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        icon = {"passed": "PASS", "failed": "FAIL", "error": "ERR ", "timeout": "TIME"}
        for c in checks:
            print(f"  [{icon[c['status']]}] {c['id']}")
        print(f"\nVerdict: {result['verdict']} ({'; '.join(result['reasons'])})")
        for c in result["blocking"] + result["inconclusive"]:
            first = (c.get("message") or "").splitlines()[:1]
            print(f"  - {c['id']}: {first[0] if first else ''}")
    return {"PASS": 0, "BLOCK": 1}.get(result["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
