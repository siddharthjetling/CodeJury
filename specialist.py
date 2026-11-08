"""Shared logic for the specialist agents (Correctness, Security, Performance).

investigate() does the real work and has no uAgents dependency:
  1. Run the verifier's protected tests for this category, if any.
  2. Ask the Meta model to write a pytest module from the SPEC and the fixture
     docs only. The model never sees the diff or the implementation.
  3. Run the generated tests in the Docker sandbox.
  4. Return gate checks (see policy_gate.py) plus the generated test source.

Generated tests that fail to load are retried once with the error, then
discarded. Tests that error in fixture setup are discarded and reported.
Assertion failures and app exceptions inside a test count as real failures.

make_agent() wraps investigate() in a uAgent.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

import jury
import policy_gate
from sandbox import runner

VERIFIER_DIR = jury.ROOT / "verifier"
PROTECTED_DIR = VERIFIER_DIR / "protected"
MIN_TESTS, MAX_TESTS = 3, 8

FOCUS = {
    "correctness": (
        "Functional requirements: response shape and required fields, ordering, computed "
        "values such as totals, empty and boundary cases, and the required status codes. "
        "Leave data isolation to the security specialist and query counts to the "
        "performance specialist."
    ),
    "security": (
        "Access control and data handling, thinking like an attacker: every way one customer "
        "could read another customer's data (IDs in query parameters, path, headers), missing "
        "or invalid API keys, and data in responses that should not be there. Use both "
        "customers (alice and bob) and assert their data never crosses. If the endpoint "
        "rejects a tampered request with 400/403/422 instead of ignoring it, that is also "
        "acceptable unless the spec says otherwise."
    ),
    "performance": (
        "Resource use under a defined workload. Count database queries with query_counter; "
        "never measure wall-clock time. Use a small and a large data size (for example 1 and "
        "100 orders) and assert the limits stated in the spec."
    ),
}

GENERATE_SYSTEM = """You are an independent verifier writing pytest tests for a code change.

You see ONLY the specification and the verifier's test fixtures. You have NOT seen the
implementation and must not guess at it. Test what the spec requires.

Your focus: {focus}

Fixtures already defined in conftest.py (use them, do not redefine them):
{fixtures}

Rules:
- Reply with ONE ```python code block containing a complete pytest module, nothing else.
- Write {min_tests}-{max_tests} test functions named test_*. Each tests one requirement and
  starts with a docstring naming it, for example: \"\"\"Spec 6: data isolation.\"\"\"
- Use only the fixtures above, the Python standard library and pytest.
  Do not import `app`, `conftest` or anything else from the repository.
- Talk to the service only over HTTP through the client fixtures.
- Every assertion must follow directly from the spec. If the spec does not say what
  should happen, do not test it.
- Give every assert a message showing the expected and actual values.
- Every test must make at least one HTTP request and assert on the response.
  Never write placeholder tests (`pass`, `assert False`, `assert True`, pytest.skip).
- No sleeps, no timing, no network access, no unseeded randomness."""

FIX_SYSTEM = """The pytest module below could not be loaded or used fixtures incorrectly.
Fix it and reply with the complete corrected module in ONE ```python code block.
Keep the same tests and the same rules: only the provided fixtures, standard library
and pytest; no imports from the repository."""

FORBIDDEN_IMPORTS = {"app", "conftest", "sandbox", "jury", "policy_gate", "llm", "config"}
CLIENT_FIXTURES = {"alice", "bob", "anon", "client_for"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "request"}


# ── Test generation ──────────────────────────────────────────────────────────
def fixture_docs() -> str:
    tree = ast.parse((VERIFIER_DIR / "conftest.py").read_text(encoding="utf-8"))
    return ast.get_docstring(tree) or ""


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip() + "\n"


def static_problems(code: str) -> str | None:
    """Return a description of why the module is unusable, or None if it looks fine."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e}"
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for n in names:
            if n.split(".")[0] in FORBIDDEN_IMPORTS:
                return f"Forbidden import: {n}. Use only the fixtures."
    tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    if not tests:
        return "No test_* functions found."
    return None


def _hollow_reason(fn: ast.FunctionDef) -> str | None:
    """Why a generated test proves nothing, or None if it looks real.

    A real test uses a client fixture, makes an HTTP call and asserts something
    that isn't a constant. Stubs like `pass` or `assert False, "placeholder"`
    would otherwise count as a fake pass or a fake defect.
    """
    params = {a.arg for a in fn.args.args}
    if not params & CLIENT_FIXTURES:
        return "does not use a client fixture"
    calls_http = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in HTTP_METHODS
        for n in ast.walk(fn)
    )
    if not calls_http:
        return "makes no HTTP request"
    asserts = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
    if not asserts:
        return "has no assertion"
    if any(isinstance(a.test, ast.Constant) for a in asserts):
        return "contains a constant assertion (placeholder)"
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "pytest"
                and n.func.attr in {"fail", "skip", "xfail"}):
            return f"calls pytest.{n.func.attr}"
    return None


def drop_hollow_tests(code: str) -> tuple[str, list[dict]]:
    """Remove test functions that don't exercise the service. Returns (code, dropped)."""
    tree = ast.parse(code)
    kept, dropped = [], []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            reason = _hollow_reason(node)
            if reason:
                dropped.append({"test": node.name, "reason": f"dropped before running: {reason}"})
                continue
        kept.append(node)
    if not dropped:
        return code, []
    tree.body = kept
    return ast.unparse(tree) + "\n", dropped


def generate_tests(category: str, spec: str) -> str:
    import llm

    system = GENERATE_SYSTEM.format(
        focus=FOCUS[category], fixtures=fixture_docs(),
        min_tests=MIN_TESTS, max_tests=MAX_TESTS,
    )
    code = extract_code(llm.chat(system, f"SPECIFICATION:\n\n{spec[:6000]}", max_tokens=3000))
    problem = static_problems(code)
    if problem:
        code = fix_tests(code, problem)
    return code


def fix_tests(code: str, error: str) -> str:
    import llm

    reply = llm.chat(
        FIX_SYSTEM,
        f"FIXTURES:\n{fixture_docs()}\n\nERROR:\n{error[-3000:]}\n\nMODULE:\n```python\n{code}```",
        max_tokens=3000,
    )
    return extract_code(reply)


# ── Investigation ────────────────────────────────────────────────────────────
def _tag(checks: list[dict], source: str) -> list[dict]:
    for c in checks:
        c["source"] = source
    return checks


def _run_generated(path: Path, repo: Path, ref: str, category: str) -> tuple[list[dict], list[dict], dict]:
    """Run a generated module. Returns (checks, discarded, raw_run)."""
    run = runner.run_verifier(repo, ref, [path])
    if run["status"] == "timeout":
        return _tag(policy_gate.checks_from_run(run, category), "generated"), [], run
    checks, discarded = [], []
    for t in run["tests"]:
        if t["outcome"] == "error":
            discarded.append({"test": t["id"], "reason": (t["message"].splitlines() or ["error"])[0][:300]})
        elif t["outcome"] in ("passed", "failed"):
            checks.append({
                "id": f"{category}:{t['id']}",
                "category": category,
                "source": "generated",
                "status": t["outcome"],
                "message": t["message"],
                "evidence": {"suite": "verifier", "ref": ref, "commit": run.get("commit"),
                             "test": t["id"], "duration_s": t.get("duration_s")},
            })
    return checks, discarded, run


def investigate(category: str, spec: str, ref: str, run_id: str,
                repo: Path = jury.DEMO_REPO, use_llm: bool = True) -> dict:
    result = {
        "category": category, "ref": ref, "run_id": run_id,
        "checks": [], "discarded": [], "notes": [],
        "generated_file": None, "model": None,
    }
    if ref not in jury.list_refs(repo):
        result["checks"] = [jury.missing_agent_check(category, f"Unknown change {ref!r}")]
        return result

    # 1. Protected tests owned by the verifier.
    protected = sorted((PROTECTED_DIR / category).glob("test_*.py"))
    if protected:
        run = runner.run_verifier(repo, ref, protected)
        result["checks"] += _tag(policy_gate.checks_from_run(run, category), "protected")

    # 2-3. Spec-first generated tests.
    if use_llm:
        try:
            import llm

            result["model"] = llm.model_settings()
            code = generate_tests(category, spec)
            out = jury.RUNS_DIR / run_id / f"test_generated_{category}.py"
            out.parent.mkdir(parents=True, exist_ok=True)

            for attempt in range(2):
                problem = static_problems(code)
                if problem is None:
                    code, hollow = drop_hollow_tests(code)
                    result["discarded"] += hollow
                    problem = static_problems(code)  # all tests may have been dropped
                if problem is None:
                    out.write_text(code, encoding="utf-8")
                    checks, discarded, run = _run_generated(out, repo, ref, category)
                    loaded = run["status"] != "error" or run["tests"]
                    if loaded and (checks or attempt == 1):
                        result["checks"] += checks
                        result["discarded"] += discarded
                        break
                    problem = (run.get("error") or "") + "\n" + (run.get("log_tail") or "")
                if attempt == 0:
                    code = fix_tests(code, problem)
                else:
                    result["notes"].append(f"Generated tests discarded: {problem.strip()[:300]}")
            result["generated_file"] = str(out) if out.exists() else None
        except Exception as e:  # LLM unavailable, bad reply, etc.
            result["notes"].append(f"Test generation failed: {type(e).__name__}: {e}"[:300])
    else:
        result["notes"].append("Test generation skipped (no LLM).")

    if not result["checks"]:
        reason = "; ".join(result["notes"]) or "No tests ran"
        result["checks"] = [jury.missing_agent_check(category, reason)]

    jury.save_run(run_id, f"{category}.json", result)
    return result


# ── uAgent wrapper ───────────────────────────────────────────────────────────
def make_agent(category: str, port: int, seed_env: str):
    from uagents import Agent, Context, Protocol

    from config import require_env
    from models import InspectRequest, InspectResponse

    name = jury.DISPLAY_NAMES[category]
    agent = Agent(
        name=f"codejury-{category}",
        seed=require_env(seed_env),
        port=port,
        mailbox=True,
        publish_agent_details=True,
    )
    protocol = Protocol(name="codejury-internal", version="1.0.0")

    @protocol.on_message(InspectRequest)
    async def handle_inspect(ctx: Context, sender: str, msg: InspectRequest):
        ctx.logger.info(f"{name}: investigating {msg.ref} (run {msg.run_id})")
        try:
            res = await asyncio.to_thread(investigate, category, msg.spec_text, msg.ref, msg.run_id)
            error = ""
        except Exception as e:
            ctx.logger.exception(f"{name} investigation crashed")
            error = f"{type(e).__name__}: {e}"
            res = {"checks": [jury.missing_agent_check(category, f"{name} crashed: {error}")]}

        checks = res["checks"]
        await ctx.send(sender, InspectResponse(
            session_id=msg.session_id,
            run_id=msg.run_id,
            agent_name=name,
            category=category,
            error=error,
            tests_run=sum(c["status"] in ("passed", "failed") for c in checks),
            tests_passed=sum(c["status"] == "passed" for c in checks),
            tests_failed=sum(c["status"] == "failed" for c in checks),
            checks_json=json.dumps(checks),
            notes_json=json.dumps(res.get("notes", []) + [
                f"Discarded {d['test']}: {d['reason']}" for d in res.get("discarded", [])
            ]),
        ))
        ctx.logger.info(f"{name}: sent {len(checks)} checks")

    agent.include(protocol)
    return agent
