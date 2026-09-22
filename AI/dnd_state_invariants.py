"""Runtime validation for persistent DnD session state.

The validator is intentionally observational: it reports contradictions at
restore/persist boundaries without mutating a live campaign. This lets old
sessions keep running while Memory v2 turns implicit assumptions into explicit
invariants.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging


KNOWN_STATES = frozenset(
    {
        "WAITING_MODE",
        "LOBBY",
        "WAITING_BACKSTORY",
        "WAITING_PLOT",
        "RESOLVING",
        "WAITING_ACTION",
        "WAITING_ROLL",
        "WAITING_POLL",
        "WAITING_HEAL",
    }
)


@dataclass(frozen=True)
class DndStateIssue:
    code: str
    message: str
    severity: str = "error"


def _int_value(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _participant_ids(session) -> set[int]:
    result = set()
    for key, row in (getattr(session, "participants", {}) or {}).items():
        raw = row.get("user_id", key) if isinstance(row, dict) else key
        value = _int_value(raw)
        if value is not None:
            result.add(value)
    return result


def _target_ids(values) -> list[int]:
    result = []
    for raw in values or []:
        value = _int_value(raw)
        if value is not None:
            result.append(value)
    return result


def _add_invalid_targets(issues, *, source: str, values, participants: set[int]) -> None:
    if not participants:
        return
    invalid = sorted({value for value in _target_ids(values) if value not in participants})
    if invalid:
        issues.append(
            DndStateIssue(
                "invalid_targets",
                f"{source} contains non-participants: {invalid}",
            )
        )


def _validate_pending_state(session, issues) -> None:
    state = str(getattr(session, "state", "") or "")
    pending_roll = getattr(session, "pending_roll", None)
    pending_poll = getattr(session, "pending_poll", None)
    pending_heal = getattr(session, "pending_heal_decision", None)
    action_prompt = getattr(session, "action_prompt_message_id", None)

    if state not in KNOWN_STATES:
        issues.append(DndStateIssue("unknown_state", f"unknown state={state!r}"))

    if state == "WAITING_ROLL" and not isinstance(pending_roll, dict):
        issues.append(
            DndStateIssue("waiting_roll_without_roll", "WAITING_ROLL has no pending_roll")
        )
    if state != "WAITING_ROLL" and pending_roll is not None:
        issues.append(
            DndStateIssue(
                "dangling_pending_roll",
                f"pending_roll exists while state={state}",
                "warning",
            )
        )

    if state == "WAITING_POLL":
        if not isinstance(pending_poll, dict):
            issues.append(
                DndStateIssue("waiting_poll_without_poll", "WAITING_POLL has no pending_poll")
            )
        if not getattr(session, "current_poll_id", None):
            issues.append(
                DndStateIssue("waiting_poll_without_id", "WAITING_POLL has no current_poll_id")
            )
    elif pending_poll is not None:
        issues.append(
            DndStateIssue(
                "dangling_pending_poll",
                f"pending_poll exists while state={state}",
                "warning",
            )
        )

    if state != "WAITING_ACTION" and action_prompt is not None:
        issues.append(
            DndStateIssue(
                "dangling_action_prompt",
                f"action prompt exists while state={state}",
                "warning",
            )
        )

    if state == "WAITING_HEAL" and not isinstance(pending_heal, dict):
        issues.append(
            DndStateIssue(
                "waiting_heal_without_decision",
                "WAITING_HEAL has no pending_heal_decision",
            )
        )


def _validate_targets(session, issues) -> None:
    participants = _participant_ids(session)
    if getattr(session, "mode", None) != "participants" and not participants:
        return

    _add_invalid_targets(
        issues,
        source="action_target_user_ids",
        values=getattr(session, "action_target_user_ids", None),
        participants=participants,
    )

    pending_roll = getattr(session, "pending_roll", None)
    if isinstance(pending_roll, dict):
        _add_invalid_targets(
            issues,
            source="pending_roll.target_user_ids",
            values=pending_roll.get("target_user_ids"),
            participants=participants,
        )

    pending_poll = getattr(session, "pending_poll", None)
    if isinstance(pending_poll, dict):
        _add_invalid_targets(
            issues,
            source="pending_poll.target_user_ids",
            values=pending_poll.get("target_user_ids"),
            participants=participants,
        )

    pending_heal = getattr(session, "pending_heal_decision", None)
    if isinstance(pending_heal, dict):
        for field in ("target_id", "owner_id"):
            value = _int_value(pending_heal.get(field))
            if value is not None and participants and value not in participants:
                issues.append(
                    DndStateIssue(
                        "invalid_heal_participant",
                        f"pending_heal_decision.{field}={value} is not a participant",
                    )
                )

    pending_actions = getattr(session, "pending_actions", {}) or {}
    if isinstance(pending_actions, dict) and participants:
        invalid = []
        for key, row in pending_actions.items():
            raw = row.get("user_id", key) if isinstance(row, dict) else key
            value = _int_value(raw)
            if value is not None and value not in participants:
                invalid.append(value)
        if invalid:
            issues.append(
                DndStateIssue(
                    "invalid_pending_action_actor",
                    f"pending_actions contain non-participants: {sorted(set(invalid))}",
                )
            )


def _validate_character_sheets(session, issues) -> None:
    sheets = getattr(session, "character_sheets", None)
    if not isinstance(sheets, dict):
        return

    participants = _participant_ids(session)
    for key, sheet in sheets.items():
        if not isinstance(sheet, dict):
            issues.append(
                DndStateIssue("invalid_character_sheet", f"character_sheets[{key!r}] is not a dict")
            )
            continue

        user_id = _int_value(key)
        if participants and user_id is not None and user_id not in participants:
            issues.append(
                DndStateIssue(
                    "orphan_character_sheet",
                    f"character sheet {user_id} is not attached to a current participant",
                    "warning",
                )
            )

        hp = _int_value(sheet.get("hp"))
        max_hp = _int_value(sheet.get("max_hp"))
        status = str(sheet.get("status") or "").casefold()

        if hp is not None and hp < 0:
            issues.append(DndStateIssue("negative_hp", f"character {key} has hp={hp}"))
        if max_hp is not None and max_hp < 1:
            issues.append(DndStateIssue("invalid_max_hp", f"character {key} has max_hp={max_hp}"))
        if hp is not None and max_hp is not None and hp > max_hp:
            issues.append(
                DndStateIssue("hp_above_max", f"character {key} has hp={hp} > max_hp={max_hp}")
            )
        if status == "alive" and hp is not None and hp <= 0:
            issues.append(
                DndStateIssue("alive_without_hp", f"character {key} is alive with hp={hp}")
            )
        if status in {"dead", "dying"} and hp is not None and hp != 0:
            issues.append(
                DndStateIssue(
                    "terminal_status_with_hp",
                    f"character {key} is {status} with hp={hp}",
                )
            )


def _validate_player_scoped_state(session, issues) -> None:
    participants = _participant_ids(session)
    if not participants:
        return

    positions = getattr(session, "player_positions", None)
    if isinstance(positions, dict):
        for key, position in positions.items():
            user_id = _int_value(key)
            if user_id is not None and user_id not in participants:
                issues.append(
                    DndStateIssue(
                        "orphan_player_position",
                        f"player_positions contains non-participant {user_id}",
                        "warning",
                    )
                )
            if isinstance(position, dict) and not str(position.get("location") or "").strip():
                issues.append(
                    DndStateIssue(
                        "empty_player_position",
                        f"player_positions[{key!r}] has no location",
                        "warning",
                    )
                )

    conditions = getattr(session, "conditions", None)
    if isinstance(conditions, dict):
        for key in conditions:
            user_id = _int_value(key)
            if user_id is not None and user_id not in participants:
                issues.append(
                    DndStateIssue(
                        "orphan_condition_owner",
                        f"conditions contains non-participant {user_id}",
                        "warning",
                    )
                )


def _validate_inventory(session, issues) -> None:
    inventories = getattr(session, "inventories", None)
    if not isinstance(inventories, dict):
        return

    artifact_owners: dict[str, set[str]] = {}
    for owner, items in inventories.items():
        if not isinstance(items, list):
            issues.append(
                DndStateIssue("invalid_inventory", f"inventories[{owner!r}] is not a list")
            )
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("kind") or "").casefold() != "artifact":
                continue
            name = " ".join(str(item.get("name") or "").casefold().split())
            if not name:
                continue
            artifact_owners.setdefault(name, set()).add(str(owner))
            quantity = _int_value(item.get("quantity"))
            if quantity is not None and quantity > 1:
                issues.append(
                    DndStateIssue(
                        "stacked_unique_artifact",
                        f"artifact {item.get('name')!r} has quantity={quantity}",
                    )
                )

    for name, owners in artifact_owners.items():
        if len(owners) > 1:
            issues.append(
                DndStateIssue(
                    "duplicate_unique_artifact",
                    f"artifact {name!r} belongs to multiple owners: {sorted(owners)}",
                )
            )


def _validate_generation_recovery(session, issues) -> None:
    request = getattr(session, "pending_generation_request", None)
    result = getattr(session, "pending_generated_result", None)
    has_request = isinstance(request, dict) and bool(request.get("prompt"))
    has_result = isinstance(result, dict) and bool(result.get("text"))

    if has_request and has_result:
        issues.append(
            DndStateIssue(
                "generation_request_and_result_overlap",
                "pending generation request and generated result are both active",
            )
        )

    if isinstance(request, dict) and request and not request.get("prompt"):
        issues.append(
            DndStateIssue(
                "generation_request_without_prompt",
                "pending_generation_request exists without prompt",
            )
        )

    if isinstance(result, dict) and result:
        phase = str(result.get("phase") or "READY").upper()
        if not result.get("text"):
            issues.append(
                DndStateIssue(
                    "generated_result_without_text",
                    "pending_generated_result exists without text",
                )
            )
        if phase == "APPLYING" and not isinstance(result.get("pre_apply_snapshot"), dict):
            issues.append(
                DndStateIssue(
                    "applying_result_without_snapshot",
                    "APPLYING generated result has no pre_apply_snapshot",
                )
            )


def validate_session_state(session) -> list[DndStateIssue]:
    """Return current state contradictions without mutating the session."""
    issues: list[DndStateIssue] = []
    _validate_pending_state(session, issues)
    _validate_targets(session, issues)
    _validate_character_sheets(session, issues)
    _validate_player_scoped_state(session, issues)
    _validate_inventory(session, issues)
    _validate_generation_recovery(session, issues)
    return issues


def validate_and_log_session_state(session, *, boundary: str) -> list[DndStateIssue]:
    """Validate once per distinct issue set and emit structured diagnostics."""
    issues = validate_session_state(session)
    fingerprint = (
        str(boundary),
        tuple((item.severity, item.code, item.message) for item in issues),
    )
    attr = "_upupa_dnd_invariant_fingerprint"
    if not issues:
        if hasattr(session, attr):
            try:
                delattr(session, attr)
            except AttributeError:
                pass
        return issues
    if getattr(session, attr, None) == fingerprint:
        return issues
    setattr(session, attr, fingerprint)

    for issue in issues:
        log = logging.error if issue.severity == "error" else logging.warning
        log(
            "DnD state invariant %s boundary=%s chat_id=%s state=%s code=%s detail=%s",
            issue.severity,
            boundary,
            getattr(session, "chat_id", None),
            getattr(session, "state", None),
            issue.code,
            issue.message,
        )
    return issues


__all__ = [
    "DndStateIssue",
    "KNOWN_STATES",
    "validate_and_log_session_state",
    "validate_session_state",
]
