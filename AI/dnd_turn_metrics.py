"""Pacing and AI-cost metrics of a party, measured instead of guessed.

The October audit asked for real numbers before touching the AI budgets again:
how many moves are resolved locally, how long a master step takes, how often a
step runs out of budget or fails. Every AI step already runs inside
``dnd_turn_budget`` and every local move commits through the local engine, so
both report here; nothing is sampled or estimated after the fact.

``turn_metrics`` is diagnostic data: not canonical, never changes
``state_revision``. ``днд метрики`` shows it; the campaign end logs a summary.
"""
from __future__ import annotations

import copy
import logging

FIELD = "turn_metrics"
_DURATIONS_KEPT = 60


def ensure(session) -> dict:
    row = getattr(session, FIELD, None)
    if not isinstance(row, dict):
        row = {}
        setattr(session, FIELD, row)
    for key in ("ai_steps", "ai_calls", "optional_calls", "exhausted", "failed", "local_ops", "local_needing_master",
                "known_tokens", "estimated_tokens"):
        row.setdefault(key, 0)
    row.setdefault("ai_seconds", 0.0)
    row.setdefault("durations", [])
    row.setdefault("local_kinds", {})
    return row


def note_ai_step(session, snapshot: dict, *, seconds: float, outcome: str = "ok") -> None:
    """One logical master step (all provider calls, repairs and parse of a turn)."""
    if session is None:
        return
    try:
        row = ensure(session)
        if not int(snapshot.get("calls") or 0) and outcome == "ok":
            return  # a scope that never reached a provider is not a master step
        row["ai_steps"] += 1
        row["ai_calls"] += int(snapshot.get("calls") or 0)
        row["optional_calls"] += int(snapshot.get("optional_calls") or 0)
        row["known_tokens"] += int(snapshot.get("known_tokens") or 0)
        row["estimated_tokens"] += int(snapshot.get("estimated_tokens") or 0)
        row["ai_seconds"] = round(float(row["ai_seconds"]) + max(0.0, float(seconds)), 3)
        row["durations"] = (list(row["durations"]) + [round(max(0.0, float(seconds)), 2)])[-_DURATIONS_KEPT:]
        if outcome == "exhausted":
            row["exhausted"] += 1
        elif outcome == "failed":
            row["failed"] += 1
    except Exception:
        logging.exception("DnD turn metrics failed chat_id=%s", getattr(session, "chat_id", None))


def note_local_op(session, kind: str, *, needs_master: bool) -> None:
    try:
        row = ensure(session)
        row["local_ops"] += 1
        row["local_needing_master"] += int(bool(needs_master))
        kinds = dict(row["local_kinds"])
        kinds[str(kind)] = int(kinds.get(str(kind), 0)) + 1
        row["local_kinds"] = kinds
    except Exception:
        logging.exception("DnD local metrics failed chat_id=%s", getattr(session, "chat_id", None))


def _percentile(values, share):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def summary(session) -> dict:
    row = getattr(session, FIELD, None) or {}
    steps = int(row.get("ai_steps") or 0)
    local = int(row.get("local_ops") or 0)
    durations = list(row.get("durations") or [])
    moves = steps + local
    return {
        "ai_steps": steps,
        "ai_calls": int(row.get("ai_calls") or 0),
        "calls_per_step": round(int(row.get("ai_calls") or 0) / steps, 2) if steps else 0.0,
        "avg_seconds": round(float(row.get("ai_seconds") or 0) / steps, 1) if steps else 0.0,
        "p90_seconds": _percentile(durations, 0.9),
        "local_ops": local,
        "local_share": round(100 * local / moves) if moves else 0,
        "local_without_master": local - int(row.get("local_needing_master") or 0),
        "exhausted": int(row.get("exhausted") or 0),
        "failed": int(row.get("failed") or 0),
        "known_tokens": int(row.get("known_tokens") or 0),
        "local_kinds": dict(row.get("local_kinds") or {}),
    }


def render(session) -> str:
    data = summary(session)
    if not data["ai_steps"] and not data["local_ops"]:
        return "📊 Метрики партии\nПока нечего считать: ходов мастера и локальных действий ещё не было."
    kinds = ", ".join(f"{kind} ×{count}" for kind, count in sorted(data["local_kinds"].items())) or "—"
    lines = [
        "📊 Метрики партии",
        f"Ходы мастера: {data['ai_steps']} · запросов к модели {data['ai_calls']} ({data['calls_per_step']} на ход)",
        f"Время хода мастера: среднее {data['avg_seconds']} с, 90% быстрее {data['p90_seconds']} с",
        f"Локальные действия без нейросети: {data['local_ops']} ({data['local_share']}% всех ходов), "
        f"из них не потребовали мастера: {data['local_without_master']}",
        f"Виды локальных действий: {kinds}",
        f"Срывы: бюджет исчерпан {data['exhausted']}, ошибка провайдера {data['failed']}",
    ]
    if data["known_tokens"]:
        lines.append(f"Известный расход токенов: {data['known_tokens']}")
    return "\n".join(lines)


def log_summary(session, reason: str = "finish") -> None:
    logging.info("DnD party metrics chat_id=%s reason=%s metrics=%s",
                 getattr(session, "chat_id", None), reason, summary(session))


def install_dnd_turn_metrics(*, state_policy) -> None:
    from AI.dnd_progression import register_archive_hook

    state_policy.add_state_field(FIELD, lambda s: copy.deepcopy(getattr(s, FIELD, None) or {}))
    register_archive_hook(lambda session, _players: log_summary(session))


__all__ = ["install_dnd_turn_metrics", "log_summary", "note_ai_step", "note_local_op", "render", "summary"]
