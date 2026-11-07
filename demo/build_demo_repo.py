"""Build the demo git repository used by CodeJury.

Creates `.demo_repo/` at the project root with three branches:
  main                    the working orders service
  ai-export-orders        AI-written export endpoint (data leak + N+1 queries)
  ai-export-orders-fixed  the corrected export endpoint

Usage:
  python demo/build_demo_repo.py           # fails if .demo_repo exists
  python demo/build_demo_repo.py --force   # rebuild from scratch
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
ROOT = DEMO_DIR.parent
TARGET = ROOT / ".demo_repo"

BRANCHES = [
    ("ai-export-orders", "Add Export Orders endpoint"),
    ("ai-export-orders-fixed", "Add Export Orders endpoint (scoped to caller, single query)"),
]

# Fixed identity and dates so commit hashes are reproducible across machines.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Demo Author",
    "GIT_AUTHOR_EMAIL": "demo@codejury.local",
    "GIT_COMMITTER_NAME": "Demo Author",
    "GIT_COMMITTER_EMAIL": "demo@codejury.local",
    "GIT_AUTHOR_DATE": "2026-11-01T12:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-11-01T12:00:00+00:00",
}


def git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=TARGET,
        check=True,
        env={**os.environ, **GIT_ENV},
        stdout=subprocess.DEVNULL,
    )


def copy_tree(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            out = dst / path.relative_to(src)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, out)


def remove_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="delete and rebuild .demo_repo")
    args = parser.parse_args()

    if TARGET.exists():
        if not args.force:
            print(f"{TARGET} already exists. Use --force to rebuild.", file=sys.stderr)
            return 1
        shutil.rmtree(TARGET, onexc=remove_readonly)

    TARGET.mkdir()
    git("init", "-b", "main")
    copy_tree(DEMO_DIR / "base", TARGET)
    git("add", "-A")
    git("commit", "-m", "Initial orders service")

    for branch, message in BRANCHES:
        git("checkout", "-b", branch, "main")
        copy_tree(DEMO_DIR / "variants" / branch, TARGET)
        git("add", "-A")
        git("commit", "-m", message)

    git("checkout", "main")
    print(f"Demo repo ready at {TARGET}")
    print("Branches: main, " + ", ".join(b for b, _ in BRANCHES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
