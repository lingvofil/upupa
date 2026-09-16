#!/usr/bin/env python3
"""Run pyflakes across tracked production Python with a shrinking legacy allowlist."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / ".github" / "pyflakes-production-allowlist.txt"


def _tracked_python_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        text=True,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _load_allowlist() -> set[str]:
    entries: set[str] = set()
    for raw_line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return entries


def _run_pyflakes(paths: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyflakes", *paths],
        cwd=REPO_ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )


def main() -> int:
    tracked = _tracked_python_files()
    production = [path for path in tracked if not path.startswith("tests/")]
    production_set = set(production)
    allowlist = _load_allowlist()

    missing = sorted(allowlist - production_set)
    if missing:
        print("Stale pyflakes allowlist entries (not tracked production files):", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 2

    guarded = sorted(production_set - allowlist)
    result = _run_pyflakes(guarded)
    if result.returncode:
        return result.returncode

    stale_allowlist: list[str] = []
    for path in sorted(allowlist):
        legacy_result = _run_pyflakes([path], capture=True)
        if legacy_result.returncode == 0:
            stale_allowlist.append(path)

    if stale_allowlist:
        print(
            "These legacy allowlist entries are clean now; remove them from "
            f"{ALLOWLIST_PATH.relative_to(REPO_ROOT)}:",
            file=sys.stderr,
        )
        for path in stale_allowlist:
            print(f"  {path}", file=sys.stderr)
        return 3

    print(
        f"pyflakes gate: {len(guarded)} production files clean; "
        f"{len(allowlist)} legacy-debt files explicitly tracked"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
