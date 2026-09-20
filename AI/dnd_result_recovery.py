"""Durable outbox for generated DnD turns.

A successful main DnD generation is persisted before it is parsed. If the process
dies in the gap between provider success and turn application, startup replays
that exact text instead of asking the model for a different scene.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import time


RESULT_PHASE_READY = "READY"
RESULT_PHASE_APPLYING = "APPLYING"

_CORE_SNAPSHOT_FIELDS = (
    "state",
    "last_roll_stat",
    "pending_roll",
    "current_poll_id",
    "pending_poll",
    "action_prompt_message_id",
    "pending_actions",
    "action_deadline",
    "action_target_user_ids",
    "recent_scene_types",
    "backstory_prompt_message_id",
    "mode_prompt_message_id",
    "lobby_message_id",
)


def _ensure(session) -> None:
    pending = getattr(session, "pending_generated_result", None)
    session.pending_generated_result = dict(pending) if isinstance(pending, dict) else {}
    try:
        session.generated_result_seq = max(0, int(getattr(session, "generated_result_seq", 0) or 0))
    except (TypeError, ValueError):
        session.generated_result_seq = 0


def _restore(session, data) -> None:
    row = data if isinstance(data, dict) else {}
    pending = row.get("pending_generated_result")
    session.pending_generated_result = copy.deepcopy(pending) if isinstance(pending, dict) else {}
    try:
        session.generated_result_seq = max(0, int(row.get("generated_result_seq", 0) or 0))
    except (TypeError, ValueError):
        session.generated_result_seq = 0
    _ensure(session)


def _generation_is_ephemeral(session) -> bool:
    try:
        return int(getattr(session, "_upupa_ephemeral_generation_depth", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _pending_text(session) -> str | None:
    _ensure(session)
    text = session.pending_generated_result.get("text")
    return str(text) if text else None


def _result_matches(session, text: str) -> bool:
    pending = getattr(session, "pending_generated_result", None)
    return bool(
        isinstance(pending, dict)
        and pending.get("text")
        and str(pending.get("text")) == str(text or "")
    )


def _new_result(session, text: str) -> dict:
    _ensure(session)
    session.generated_result_seq += 1
    payload = str(text or "")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"{session.generated_result_seq}:{digest}",
        "text": payload,
        "phase": RESULT_PHASE_READY,
        "created_at": time.time(),
        "source_state": str(getattr(session, "state", "") or ""),
    }


def _snapshot_parse_state(session) -> dict:
    row = session.to_record()
    campaign_state = copy.deepcopy(row.get("campaign_state") or {})
    if isinstance(campaign_state, dict):
        campaign_state.pop("pending_generated_result", None)
    core = {
        field: copy.deepcopy(row.get(field))
        for field in _CORE_SNAPSHOT_FIELDS
        if field in row
    }
    return {
        "core": core,
        "campaign_state": campaign_state,
    }


def _restore_parse_state(session, snapshot, state_policy) -> bool:
    if not isinstance(snapshot, dict):
        return False
    pending = copy.deepcopy(getattr(session, "pending_generated_result", {}) or {})
    core = snapshot.get("core") or {}
    if isinstance(core, dict):
        for field in _CORE_SNAPSHOT_FIELDS:
            if field in core:
                setattr(session, field, copy.deepcopy(core[field]))
    campaign_state = snapshot.get("campaign_state")
    if isinstance(campaign_state, dict):
        state_policy.restore(session, copy.deepcopy(campaign_state))
    session.pending_generated_result = pending
    _ensure(session)
    return True


def _clear_pending(session) -> bool:
    _ensure(session)
    if not session.pending_generated_result:
        return False
    session.pending_generated_result = {}
    return True


def _normalize_ready_group_replay(session) -> None:
    """Mirror the normal group-action caller state just before parse."""
    if getattr(session, "state", None) != "RESOLVING":
        return
    if not (getattr(session, "pending_actions", {}) or {}):
        return
    session.action_prompt_message_id = None
    session.pending_actions = {}
    session.action_deadline = None
    session.action_target_user_ids = []


async def _resume_pending_result(dnd, bot, session, state_policy) -> None:
    _ensure(session)
    pending = copy.deepcopy(session.pending_generated_result)
    if not pending or not pending.get("text"):
        return

    phase = str(pending.get("phase") or RESULT_PHASE_READY).upper()
    if phase == RESULT_PHASE_APPLYING and getattr(session, "state", None) != "RESOLVING":
        # The downstream parser already committed a durable next state (ROLL,
        # POLL, INPUT, etc.) and the process died only before the outbox cleanup.
        if _clear_pending(session):
            dnd.persist_dnd_sessions()
        return

    if phase == RESULT_PHASE_APPLYING:
        snapshot = pending.get("pre_apply_snapshot")
        if _restore_parse_state(session, snapshot, state_policy):
            pending["phase"] = RESULT_PHASE_READY
            pending.pop("pre_apply_snapshot", None)
            session.pending_generated_result = pending
            dnd.persist_dnd_sessions()
    else:
        _normalize_ready_group_replay(session)
        dnd.persist_dnd_sessions()

    try:
        await dnd.parse_and_execute_turn(bot, session.chat_id, pending["text"])
    except Exception:
        logging.exception(
            "DnD durable result replay failed chat_id=%s result_id=%s",
            getattr(session, "chat_id", None),
            pending.get("id"),
        )


def configure_dnd_result_recovery(dnd_module=None, *, state_policy=None) -> None:
    if dnd_module is None:
        from AI import dnd as dnd_module

    dnd = dnd_module
    if getattr(dnd, "_upupa_dnd_result_recovery_configured", False):
        return

    if state_policy is None:
        router = getattr(dnd, "dnd_router", None)
        state_policy = getattr(router, "_upupa_dnd_campaign_state_policy", None)
    if state_policy is None:
        raise RuntimeError("DnD result recovery requires configured campaign state policy")

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field(
        "pending_generated_result",
        lambda session: copy.deepcopy(getattr(session, "pending_generated_result", {}) or {}),
    )
    state_policy.add_state_field(
        "generated_result_seq",
        lambda session: int(getattr(session, "generated_result_seq", 0) or 0),
    )
    state_policy.add_restore_hook(_restore)

    original_generate = dnd.generate_session_response

    async def generate_session_response(session, prompt):
        _ensure(session)
        if not _generation_is_ephemeral(session):
            existing = _pending_text(session)
            if existing:
                logging.warning(
                    "DnD reusing durable generated result chat_id=%s result_id=%s",
                    getattr(session, "chat_id", None),
                    session.pending_generated_result.get("id"),
                )
                return existing

        result = await original_generate(session, prompt)
        if (
            not _generation_is_ephemeral(session)
            and getattr(dnd, "dnd_sessions", {}).get(getattr(session, "chat_id", None)) is session
        ):
            session.pending_generated_result = _new_result(session, result)
            dnd.persist_dnd_sessions()
        return result

    dnd.generate_session_response = generate_session_response

    original_parse = dnd.parse_and_execute_turn

    async def parse_and_execute_turn(bot, chat_id, text_response):
        session = dnd.dnd_sessions.get(chat_id)
        if session is None or not _result_matches(session, text_response):
            return await original_parse(bot, chat_id, text_response)

        pending = session.pending_generated_result
        if str(pending.get("phase") or RESULT_PHASE_READY).upper() != RESULT_PHASE_APPLYING:
            pending["phase"] = RESULT_PHASE_APPLYING
            pending["pre_apply_snapshot"] = _snapshot_parse_state(session)
            dnd.persist_dnd_sessions()

        try:
            result = await original_parse(bot, chat_id, text_response)
        except Exception:
            if dnd.dnd_sessions.get(chat_id) is session:
                dnd.persist_dnd_sessions()
            raise

        current = dnd.dnd_sessions.get(chat_id)
        if current is session and _result_matches(current, text_response):
            _clear_pending(current)
            dnd.persist_dnd_sessions()
        return result

    dnd.parse_and_execute_turn = parse_and_execute_turn

    original_open_action = dnd.open_action_window

    async def open_action_window(bot, chat_id, target_user_ids=None):
        session = dnd.dnd_sessions.get(chat_id)
        if session is not None:
            _clear_pending(session)
        return await original_open_action(bot, chat_id, target_user_ids=target_user_ids)

    dnd.open_action_window = open_action_window

    original_restore_sessions = dnd.restore_dnd_sessions

    def restore_dnd_sessions(bot):
        restored = original_restore_sessions(bot)
        dirty = False
        for session in list(getattr(dnd, "dnd_sessions", {}).values()):
            _ensure(session)
            pending = session.pending_generated_result
            if not pending or not pending.get("text"):
                continue
            phase = str(pending.get("phase") or RESULT_PHASE_READY).upper()
            if phase == RESULT_PHASE_APPLYING and getattr(session, "state", None) != "RESOLVING":
                session.pending_generated_result = {}
                dirty = True
                continue
            dnd._start_background_task(
                _resume_pending_result(dnd, bot, session, state_policy),
                name=f"dnd-result-replay:{session.chat_id}:{pending.get('id')}",
            )
        if dirty:
            dnd.persist_dnd_sessions()
        return restored

    dnd.restore_dnd_sessions = restore_dnd_sessions
    dnd._upupa_dnd_result_recovery_configured = True


__all__ = [
    "RESULT_PHASE_READY",
    "RESULT_PHASE_APPLYING",
    "_snapshot_parse_state",
    "_restore_parse_state",
    "_resume_pending_result",
    "configure_dnd_result_recovery",
]
