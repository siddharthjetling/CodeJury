"""Run tests against one commit of a repository inside a locked-down Docker container.

Two kinds of runs:
  run_baseline(repo, ref)               the repository's own tests (tests/)
  run_verifier(repo, ref, test_files)   verifier-owned tests + verifier/conftest.py

Container limits: no network, read-only filesystem, code and tests mounted
read-only, CPU/memory/process limits, all capabilities dropped, non-root user,
and a wall-clock timeout. No host environment variables (API keys) are passed in.

Both functions return a JSON-serializable dict:
{
  "suite": "baseline" | "verifier",
  "ref": "ai-export-orders", "commit": "<sha>",
  "status": "completed" | "error" | "timeout",
  "error": null | "<why the run produced no usable result>",
  "tests": [{"id", "outcome": "passed"|"failed"|"error"|"skipped", "message", "duration_s"}],
  "counts": {"passed", "failed", "error", "skipped"},
  "exit_code", "duration_s", "log_tail", "image", "limits", "command"
}

CLI:
  python -m sandbox.runner <ref> [--baseline] [test files...]
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIR = ROOT / "sandbox"
VERIFIER_DIR = ROOT / "verifier"
DEFAULT_REPO = ROOT / ".demo_repo"

IMAGE = "codejury-sandbox:py311"
LIMITS = {"cpus": "1", "memory": "512m", "pids": "256", "timeout_s": 120}
LOG_TAIL_CHARS = 4000


class SandboxError(RuntimeError):
    pass


# ── Docker helpers ───────────────────────────────────────────────────────────
def _docker(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def ensure_image() -> None:
    try:
        if _docker("version", "--format", "{{.Server.Version}}", timeout=30).returncode != 0:
            raise SandboxError("Docker daemon is not running")
    except FileNotFoundError as e:
        raise SandboxError("Docker CLI not found") from e
    if _docker("image", "inspect", IMAGE).returncode == 0:
        return
    build = _docker("build", "-t", IMAGE, str(SANDBOX_DIR), timeout=900)
    if build.returncode != 0:
        raise SandboxError(f"Failed to build {IMAGE}:\n{build.stderr[-LOG_TAIL_CHARS:]}")


# ── Git helpers ──────────────────────────────────────────────────────────────
def resolve_commit(repo: Path, ref: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SandboxError(f"Unknown ref {ref!r} in {repo}: {r.stderr.strip()}")
    return r.stdout.strip()


def export_commit(repo: Path, commit: str, dest: Path) -> None:
    """Write a clean copy of the commit's files (no .git) to dest."""
    r = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", commit], capture_output=True)
    if r.returncode != 0:
        raise SandboxError(f"git archive failed: {r.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
        tar.extractall(dest, filter="data")


# ── JUnit parsing ────────────────────────────────────────────────────────────
def parse_junit(path: Path) -> list[dict]:
    tests = []
    for case in ET.parse(path).getroot().iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome = {"failure": "failed"}.get(tag, tag)
                message = (el.get("message") or "") + ("\n" + el.text if el.text else "")
                break
        tests.append({
            "id": f"{classname.replace('.', '/')}.py::{name}" if classname else name,
            "outcome": outcome,
            "message": message.strip()[:2000],
            "duration_s": float(case.get("time") or 0),
        })
    return tests


# ── Core run ─────────────────────────────────────────────────────────────────
def _run(suite: str, repo: Path, ref: str, test_files: list[Path] | None, timeout_s: int) -> dict:
    result: dict = {
        "suite": suite, "ref": ref, "commit": None, "status": "error", "error": None,
        "tests": [], "counts": {}, "exit_code": None, "duration_s": 0.0,
        "log_tail": "", "image": IMAGE, "limits": {**LIMITS, "timeout_s": timeout_s},
        "command": None,
    }
    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="codejury-"))
    name = f"codejury-{uuid.uuid4().hex[:12]}"
    try:
        ensure_image()
        commit = resolve_commit(repo, ref)
        result["commit"] = commit

        work, verifier, out = tmp / "work", tmp / "verifier", tmp / "out"
        for d in (work, verifier, out):
            d.mkdir()
        export_commit(repo, commit, work)

        mounts = [
            f"type=bind,source={work},target=/work,readonly",
            f"type=bind,source={out},target=/out",
        ]
        if suite == "baseline":
            if not (work / "tests").is_dir():
                raise SandboxError("Repository has no tests/ directory")
            pytest_args = ["/work/tests", "-p", "no:cacheprovider", "--rootdir", "/work"]
            workdir = "/work"
        else:
            if not test_files:
                raise SandboxError("No verifier test files given")
            shutil.copyfile(VERIFIER_DIR / "conftest.py", verifier / "conftest.py")
            shutil.copyfile(VERIFIER_DIR / "pytest.ini", verifier / "pytest.ini")
            (verifier / "tests").mkdir()
            for f in test_files:
                dest = verifier / "tests" / Path(f).name
                if dest.exists():
                    raise SandboxError(f"Duplicate test file name: {dest.name}")
                shutil.copyfile(f, dest)
            mounts.append(f"type=bind,source={verifier},target=/verifier,readonly")
            # -c pins the verifier's config; candidate pytest.ini/pyproject/conftest are ignored.
            pytest_args = ["/verifier/tests", "-c", "/verifier/pytest.ini", "--rootdir", "/verifier"]
            workdir = "/verifier"

        cmd = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--cpus", LIMITS["cpus"], "--memory", LIMITS["memory"],
            "--memory-swap", LIMITS["memory"], "--pids-limit", LIMITS["pids"],
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-e", "PYTHONPATH=/work", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "-e", "ORDERS_DB_PATH=/tmp/orders.db",
            "-w", workdir,
        ]
        for m in mounts:
            cmd += ["--mount", m]
        cmd += [IMAGE, "python", "-m", "pytest", *pytest_args,
                "-q", "-rA", "--junitxml=/out/report.xml", "--basetemp=/tmp/pytest"]
        result["command"] = " ".join(cmd)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout_s)
        except subprocess.TimeoutExpired as e:
            _docker("kill", name, timeout=30)
            result["status"] = "timeout"
            result["error"] = f"Test run exceeded {timeout_s}s"
            result["log_tail"] = (e.stdout or "")[-LOG_TAIL_CHARS:] if isinstance(e.stdout, str) else ""
            return result

        result["exit_code"] = proc.returncode
        result["log_tail"] = (proc.stdout + proc.stderr)[-LOG_TAIL_CHARS:]
        report = out / "report.xml"
        # pytest exit codes: 0 all passed, 1 some failed; anything else means no usable verdict.
        if proc.returncode not in (0, 1) or not report.exists():
            result["error"] = f"pytest exited with code {proc.returncode} (no usable report)"
            if report.exists():
                result["tests"] = parse_junit(report)
            return result

        result["tests"] = parse_junit(report)
        if not result["tests"]:
            result["error"] = "No tests were collected"
            return result
        result["status"] = "completed"
        return result
    except SandboxError as e:
        result["error"] = str(e)
        return result
    finally:
        counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        for t in result["tests"]:
            counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
        result["counts"] = counts
        result["duration_s"] = round(time.monotonic() - started, 2)
        shutil.rmtree(tmp, ignore_errors=True)


def run_baseline(repo: Path, ref: str, timeout_s: int = LIMITS["timeout_s"]) -> dict:
    return _run("baseline", Path(repo), ref, None, timeout_s)


def run_verifier(repo: Path, ref: str, test_files: list[Path], timeout_s: int = LIMITS["timeout_s"]) -> dict:
    return _run("verifier", Path(repo), ref, [Path(f) for f in test_files], timeout_s)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Run tests for a ref in the sandbox")
    p.add_argument("ref")
    p.add_argument("tests", nargs="*", type=Path, help="verifier test files")
    p.add_argument("--baseline", action="store_true", help="run the repo's own tests")
    p.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    a = p.parse_args()
    res = run_baseline(a.repo, a.ref) if a.baseline else run_verifier(a.repo, a.ref, a.tests)
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["status"] == "completed" else 2)
