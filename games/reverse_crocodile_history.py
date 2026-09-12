"""Persistent no-repeat cycles for themed reverse Crocodile modes."""

from __future__ import annotations

import json
import logging
import random
import threading
from pathlib import Path
from typing import Iterable

from core.paths import CROCODILE_STATE_PATH


HISTORY_VERSION = 1
HISTORY_CARRYOVER = 20
HISTORY_PATH = Path(CROCODILE_STATE_PATH).with_name("reverse_crocodile_history.json")
_HISTORY_LOCK = threading.Lock()


def normalize_answer(value: str) -> str:
    return "".join(
        char
        for char in str(value or "").casefold().replace("ё", "е")
        if char.isalnum()
    )


def _load_history() -> dict[str, list[str]]:
    if not HISTORY_PATH.is_file():
        return {}
    try:
        payload = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != HISTORY_VERSION:
            return {}
        modes = payload.get("modes", {})
        if not isinstance(modes, dict):
            return {}
        return {
            str(mode): [str(item) for item in used if str(item).strip()]
            for mode, used in modes.items()
            if isinstance(used, list)
        }
    except Exception:
        logging.exception("[rcroc-history] failed to load history path=%s", HISTORY_PATH)
        return {}


def _write_history(history: dict[str, list[str]]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = HISTORY_PATH.with_suffix(HISTORY_PATH.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(
            {"version": HISTORY_VERSION, "modes": history},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temp_path.replace(HISTORY_PATH)


def pick_from_pool(mode: str, pool: Iterable[str]) -> str:
    """Pick randomly without repeats until the mode pool is exhausted.

    Histories are persisted per mode. At cycle rollover a small tail stays
    blocked, mirroring regular Crocodile's persistent shuffle guard so the
    boundary between cycles cannot immediately repeat the latest answers.
    """
    unique: dict[str, str] = {}
    for item in pool:
        value = str(item).strip()
        key = normalize_answer(value)
        if key and key not in unique:
            unique[key] = value
    if not unique:
        raise ValueError(f"empty reverse Crocodile pool: {mode}")

    mode_key = str(mode)
    with _HISTORY_LOCK:
        history = _load_history()
        prior = history.get(mode_key, [])
        used: list[str] = []
        seen: set[str] = set()
        for key in prior:
            if key in unique and key not in seen:
                used.append(key)
                seen.add(key)

        available = [key for key in unique if key not in seen]
        if not available:
            carry_count = min(HISTORY_CARRYOVER, max(0, len(unique) - 1))
            used = used[-carry_count:] if carry_count else []
            seen = set(used)
            available = [key for key in unique if key not in seen]

        chosen_key = random.choice(available)
        used.append(chosen_key)
        history[mode_key] = used
        try:
            _write_history(history)
        except Exception:
            logging.exception(
                "[rcroc-history] failed to persist mode=%s path=%s",
                mode_key,
                HISTORY_PATH,
            )
        return unique[chosen_key]
