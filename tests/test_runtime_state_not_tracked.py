"""Production runtime state must never be tracked by git.

The deploy does `git reset --hard <sha>` in the live app directory, where state
files live next to the code (core/paths.DATA_DIR == PROJECT_ROOT). A tracked
state file is silently overwritten by its committed copy on every deploy.

Regression: an empty `dnd_sessions_campaigns.json` written by a local test run
was committed on 2026-10-11 and wiped the production DnD archive (all heroes,
inventories and finished campaigns).
"""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Root-level files that are code/config, not runtime state.
ALLOWED_ROOT_DATA = {"requirements.txt", "requirements-test.txt"}
STATE_SUFFIXES = (".json", ".jsonl", ".db", ".sqlite", ".sqlite3", ".log")


def _tracked_root_files() -> list[str]:
    try:
        output = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git metadata is unavailable")
    return [line for line in output.splitlines() if line and "/" not in line]


def test_no_runtime_state_file_is_tracked_in_the_app_root():
    tracked = [name for name in _tracked_root_files()
               if name.endswith(STATE_SUFFIXES) and name not in ALLOWED_ROOT_DATA]
    assert tracked == [], (
        "Runtime state is tracked by git and would overwrite production on deploy: "
        f"{tracked}. Remove it with `git rm --cached` and add it to .gitignore."
    )


def test_known_state_paths_are_ignored_by_git():
    from core import paths
    from AI import dnd

    candidates = {Path(value).name for name, value in vars(paths).items()
                  if name.endswith("_PATH") and isinstance(value, Path) and Path(value).parent == paths.DATA_DIR}
    state = Path(dnd.DND_STATE_PATH)
    candidates |= {state.name, f"{state.stem}_campaigns.json"}
    try:
        result = subprocess.run(["git", "check-ignore", "--no-index", *sorted(candidates)], cwd=ROOT,
                                capture_output=True, text=True)
    except OSError:
        pytest.skip("git is unavailable")
    ignored = set(result.stdout.split())
    assert f"{state.stem}_campaigns.json" in ignored
    assert state.name in ignored
