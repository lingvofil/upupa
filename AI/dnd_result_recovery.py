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
from types import SimpleNamespace


RESULT_PHASE_READY = "READY"
RESULT_PHASE_APPLYING = "APPLYING"
EFFECT_IN_FLIGHT = "IN_FLIGHT"
EFFECT_DONE = "DONE"

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
    request = getattr(session, "pending_generation_request", None)
    session.pending_generation_request = dict(request) if isinstance(request, dict) else {}
    try:
        session.generated_result_seq = max(0, int(getattr(session, "generated_result_seq", 0) or 0))
    except (TypeError, ValueError):
        session.generated_result_seq = 0


def _restore(session, data) -> None:
    row = data if isinstance(data, dict) else {}
    pending = row.get("pending_generated_result")
    session.pending_generated_result = copy.deepcopy(pending) if isinstance(pending, dict) else {}
    request = row.get("pending_generation_request")
    session.pending_generation_request = copy.deepcopy(request) if isinstance(request, dict) else {}
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


def _pending_generation_prompt(session) -> str | None:
    _ensure(session)
    prompt = session.pending_generation_request.get("prompt")
    return str(prompt) if prompt else None


def _new_generation_request(session, prompt: str) -> dict:
    payload = str(prompt or "")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    conversation = getattr(session, "conversation", None)
    conversation_size = len(conversation) if isinstance(conversation, list) else None
    return {
        "id": f"gen:{int(time.time() * 1000)}:{digest}",
        "prompt": payload,
        "created_at": time.time(),
        "source_state": str(getattr(session, "state", "") or ""),
        "conversation_size": conversation_size,
    }


def _clear_generation_request(session) -> bool:
    _ensure(session)
    if not session.pending_generation_request:
        return False
    session.pending_generation_request = {}
    return True


def _generation_effects(effects) -> list[dict]:
    rows = []
    for index, raw in enumerate(effects or []):
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        method = str(item.get("method") or "").strip()
        if method not in {"send_message", "stop_poll"}:
            continue
        item["index"] = len(rows)
        item["method"] = method
        item.setdefault("status", "PENDING")
        rows.append(item)
    return rows


def reserve_generation_request(
    session,
    prompt: str,
    *,
    kind: str = "GENERATION",
    effects=None,
) -> bool:
    """Reserve an exact continuation request before any provider/network work."""
    _ensure(session)
    if _pending_text(session):
        return False
    existing = _pending_generation_prompt(session)
    if existing:
        return existing == str(prompt or "")
    request = _new_generation_request(session, str(prompt or ""))
    request["kind"] = str(kind or "GENERATION")
    request["telegram_effects"] = _generation_effects(effects)
    session.pending_generation_request = request
    return True


def transition_to_generation_request(
    session,
    prompt: str,
    *,
    kind: str,
    effects=None,
) -> bool:
    """Commit the current parsed result and atomically reserve its successor."""
    _ensure(session)
    existing = _pending_generation_prompt(session)
    if existing:
        return existing == str(prompt or "")
    parent_id = (getattr(session, "pending_generated_result", {}) or {}).get("id")
    request = _new_generation_request(session, str(prompt or ""))
    request["kind"] = str(kind or "GENERATION")
    request["parent_result_id"] = parent_id
    request["telegram_effects"] = _generation_effects(effects)
    session.pending_generation_request = request
    session.pending_generated_result = {}
    return True


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
        "telegram_effects": [],
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


def _unwrap_transport(bot):
    current = bot
    seen = set()
    while hasattr(current, "_bot") and id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "_bot", None)
        if nested is None or nested is current:
            break
        current = nested
    return current


def _synthetic_message(effect):
    result = effect.get("result") if isinstance(effect, dict) else {}
    result = result if isinstance(result, dict) else {}
    chat_id = result.get("chat_id")
    return SimpleNamespace(
        message_id=result.get("message_id"),
        chat=SimpleNamespace(id=chat_id),
    )


def _synthetic_poll_message(effect):
    result = effect.get("result") if isinstance(effect, dict) else {}
    result = result if isinstance(result, dict) else {}
    return SimpleNamespace(
        message_id=result.get("message_id"),
        chat=SimpleNamespace(id=result.get("chat_id")),
        poll=SimpleNamespace(id=result.get("poll_id")),
    )


class _DurableBotProxy:
    """Persist Telegram sends belonging to one durable generated result."""

    _upupa_dnd_side_effect_proxy = True

    def __init__(self, bot, dnd, session):
        self._transport = _unwrap_transport(bot)
        self._dnd = dnd
        self._session = session
        self._cursor = 0

    def __getattr__(self, name):
        return getattr(self._transport, name)

    def _effect(self, method):
        pending = getattr(self._session, "pending_generated_result", {}) or {}
        effects = pending.setdefault("telegram_effects", [])
        index = self._cursor
        self._cursor += 1
        if index < len(effects):
            effect = effects[index]
            if not isinstance(effect, dict) or effect.get("method") != method:
                del effects[index:]
                effect = {"index": index, "method": method, "status": EFFECT_IN_FLIGHT}
                effects.append(effect)
                self._dnd.persist_dnd_sessions()
            return effect
        effect = {"index": index, "method": method, "status": EFFECT_IN_FLIGHT}
        effects.append(effect)
        self._dnd.persist_dnd_sessions()
        return effect

    def _mark_in_flight(self, effect):
        if effect.get("status") != EFFECT_IN_FLIGHT:
            effect["status"] = EFFECT_IN_FLIGHT
            self._dnd.persist_dnd_sessions()

    def _mark_done(self, effect, result):
        effect["status"] = EFFECT_DONE
        effect["result"] = result
        effect["completed_at"] = time.time()
        self._dnd.persist_dnd_sessions()

    async def send_message(self, chat_id, text, **kwargs):
        effect = self._effect("send_message")
        if effect.get("status") == EFFECT_DONE:
            return _synthetic_message(effect)
        self._mark_in_flight(effect)
        result = await self._transport.send_message(chat_id, text, **kwargs)
        resolved_chat_id = getattr(getattr(result, "chat", None), "id", None)
        self._mark_done(
            effect,
            {
                "message_id": getattr(result, "message_id", None),
                "chat_id": resolved_chat_id if resolved_chat_id is not None else chat_id,
            },
        )
        return result

    async def send_poll(self, *args, **kwargs):
        effect = self._effect("send_poll")
        if effect.get("status") == EFFECT_DONE:
            return _synthetic_poll_message(effect)
        self._mark_in_flight(effect)
        result = await self._transport.send_poll(*args, **kwargs)
        chat_id = getattr(getattr(result, "chat", None), "id", None)
        if chat_id is None:
            chat_id = kwargs.get("chat_id")
            if chat_id is None and args:
                chat_id = args[0]
        self._mark_done(
            effect,
            {
                "message_id": getattr(result, "message_id", None),
                "chat_id": chat_id,
                "poll_id": getattr(getattr(result, "poll", None), "id", None),
            },
        )
        return result


async def _deliver_generation_effects(dnd, bot, session) -> bool:
    _ensure(session)
    request = session.pending_generation_request
    effects = request.get("telegram_effects") or []
    if not effects:
        return True
    transport = _unwrap_transport(bot)
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        if effect.get("status") == EFFECT_DONE:
            continue
        method = effect.get("method")
        effect["status"] = EFFECT_IN_FLIGHT
        dnd.persist_dnd_sessions()
        try:
            if method == "send_message":
                result = await transport.send_message(
                    effect.get("chat_id", session.chat_id),
                    str(effect.get("text") or ""),
                    **dict(effect.get("kwargs") or {}),
                )
                effect["result"] = {
                    "message_id": getattr(result, "message_id", None),
                    "chat_id": getattr(getattr(result, "chat", None), "id", None)
                    or effect.get("chat_id", session.chat_id),
                }
            elif method == "stop_poll":
                await transport.stop_poll(
                    chat_id=effect.get("chat_id", session.chat_id),
                    message_id=int(effect["message_id"]),
                )
                effect["result"] = {"stopped": True}
            else:
                effect["result"] = {"skipped": True}
        except Exception as exc:
            if effect.get("best_effort"):
                logging.warning(
                    "DnD generation side effect failed but is best-effort chat_id=%s method=%s error=%s",
                    getattr(session, "chat_id", None),
                    method,
                    exc,
                )
                effect["result"] = {"failed": True, "error": str(exc)[:200]}
            else:
                logging.exception(
                    "DnD generation side effect failed chat_id=%s method=%s",
                    getattr(session, "chat_id", None),
                    method,
                )
                dnd.persist_dnd_sessions()
                return False
        effect["status"] = EFFECT_DONE
        effect["completed_at"] = time.time()
        dnd.persist_dnd_sessions()
    return True


async def continue_pending_generation(dnd, bot, session) -> bool:
    """Finish durable pre-generation side effects, generate, then parse."""
    _ensure(session)
    if not _pending_generation_prompt(session):
        return False
    if not await _deliver_generation_effects(dnd, bot, session):
        return False
    return await _resume_pending_generation(dnd, bot, session)


async def _resume_pending_generation(dnd, bot, session) -> bool:
    _ensure(session)
    prompt = _pending_generation_prompt(session)
    if not prompt:
        return False
    if bool(getattr(session, "_upupa_generation_call_active", False)):
        return True
    if not await _deliver_generation_effects(dnd, bot, session):
        return False
    session.state = "RESOLVING"
    dnd.persist_dnd_sessions()
    try:
        response = await dnd.generate_session_response(session, prompt)
        await dnd.parse_and_execute_turn(bot, session.chat_id, response)
        return True
    except Exception:
        logging.exception(
            "DnD pending generation retry failed chat_id=%s request_id=%s",
            getattr(session, "chat_id", None),
            session.pending_generation_request.get("id"),
        )
        return False


async def retry_pending_recovery(dnd, bot, session) -> bool:
    _ensure(session)
    router = getattr(dnd, "dnd_router", None)
    state_policy = getattr(router, "_upupa_dnd_campaign_state_policy", None)
    if _pending_text(session):
        if state_policy is None:
            return False
        await _resume_pending_result(dnd, bot, session, state_policy)
        return True
    if _pending_generation_prompt(session):
        return await _resume_pending_generation(dnd, bot, session)
    return False


async def _resume_pending_result(dnd, bot, session, state_policy) -> None:
    _ensure(session)
    pending = copy.deepcopy(session.pending_generated_result)
    if not pending or not pending.get("text"):
        return

    phase = str(pending.get("phase") or RESULT_PHASE_READY).upper()
    if phase == RESULT_PHASE_APPLYING:
        # Even if a downstream state such as WAITING_ROLL was already persisted,
        # the process may have died before one of its Telegram sends completed.
        # Restore the local pre-apply snapshot and replay the exact response.
        # Telegram effects already marked DONE are suppressed by _DurableBotProxy.
        snapshot = pending.get("pre_apply_snapshot")
        if _restore_parse_state(session, snapshot, state_policy):
            pending = session.pending_generated_result
            pending["phase"] = RESULT_PHASE_READY
            pending.pop("pre_apply_snapshot", None)
            session.pending_generated_result = pending
            dnd.persist_dnd_sessions()
        else:
            logging.error(
                "DnD durable result missing pre-apply snapshot chat_id=%s result_id=%s",
                getattr(session, "chat_id", None),
                pending.get("id"),
            )
            return
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
        "pending_generation_request",
        lambda session: copy.deepcopy(getattr(session, "pending_generation_request", {}) or {}),
    )
    state_policy.add_state_field(
        "generated_result_seq",
        lambda session: int(getattr(session, "generated_result_seq", 0) or 0),
    )
    state_policy.add_restore_hook(_restore)

    original_generate = dnd.generate_session_response

    async def generate_session_response(session, prompt):
        _ensure(session)
        if _generation_is_ephemeral(session):
            return await original_generate(session, prompt)

        existing = _pending_text(session)
        if existing:
            parse_depth = int(getattr(session, "_upupa_durable_parse_depth", 0) or 0)
            if parse_depth:
                raise RuntimeError(
                    "Nested DnD generation requires transition_to_generation_request()"
                )
            logging.warning(
                "DnD reusing durable generated result chat_id=%s result_id=%s",
                getattr(session, "chat_id", None),
                session.pending_generated_result.get("id"),
            )
            return existing

        stored_prompt = _pending_generation_prompt(session)
        if stored_prompt:
            effective_prompt = stored_prompt
            if str(prompt or "") != stored_prompt:
                logging.warning(
                    "DnD preserving earlier generation request chat_id=%s request_id=%s",
                    getattr(session, "chat_id", None),
                    session.pending_generation_request.get("id"),
                )
            rewind_to = session.pending_generation_request.get("conversation_size")
            if rewind_to is not None and hasattr(dnd, "_rewind_session_conversation"):
                if dnd._rewind_session_conversation(session, rewind_to):
                    dnd.persist_dnd_sessions()
        else:
            effective_prompt = str(prompt or "")
            reserve_generation_request(session, effective_prompt)
            dnd.persist_dnd_sessions()

        session._upupa_generation_call_active = True
        try:
            result = await original_generate(session, effective_prompt)
        except Exception:
            if getattr(dnd, "dnd_sessions", {}).get(getattr(session, "chat_id", None)) is session:
                dnd.persist_dnd_sessions()
            raise
        finally:
            try:
                del session._upupa_generation_call_active
            except AttributeError:
                pass

        if getattr(dnd, "dnd_sessions", {}).get(getattr(session, "chat_id", None)) is session:
            session.pending_generated_result = _new_result(session, result)
            session.pending_generation_request = {}
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
            pending.setdefault("telegram_effects", [])
            dnd.persist_dnd_sessions()

        depth = int(getattr(session, "_upupa_durable_parse_depth", 0) or 0)
        session._upupa_durable_parse_depth = depth + 1
        durable_bot = _DurableBotProxy(bot, dnd, session)
        try:
            result = await original_parse(durable_bot, chat_id, text_response)
        except Exception:
            if dnd.dnd_sessions.get(chat_id) is session:
                dnd.persist_dnd_sessions()
            raise
        finally:
            if depth:
                session._upupa_durable_parse_depth = depth
            else:
                try:
                    del session._upupa_durable_parse_depth
                except AttributeError:
                    pass

        current = dnd.dnd_sessions.get(chat_id)
        if current is session and _result_matches(current, text_response):
            _clear_pending(current)
            dnd.persist_dnd_sessions()
        return result

    dnd.parse_and_execute_turn = parse_and_execute_turn

    original_open_action = dnd.open_action_window

    async def open_action_window(bot, chat_id, target_user_ids=None):
        result = await original_open_action(bot, chat_id, target_user_ids=target_user_ids)
        session = dnd.dnd_sessions.get(chat_id)
        parse_depth = int(getattr(session, "_upupa_durable_parse_depth", 0) or 0) if session else 0
        if session is not None and not parse_depth:
            changed = _clear_pending(session)
            changed = _clear_generation_request(session) or changed
            if changed:
                dnd.persist_dnd_sessions()
        return result

    dnd.open_action_window = open_action_window

    original_restore_sessions = dnd.restore_dnd_sessions

    def restore_dnd_sessions(bot):
        restored = original_restore_sessions(bot)
        for session in list(getattr(dnd, "dnd_sessions", {}).values()):
            _ensure(session)
            pending = session.pending_generated_result
            if pending and pending.get("text"):
                dnd._start_background_task(
                    _resume_pending_result(dnd, bot, session, state_policy),
                    name=f"dnd-result-replay:{session.chat_id}:{pending.get('id')}",
                )
                continue
            request = session.pending_generation_request
            if request and request.get("prompt"):
                dnd._start_background_task(
                    continue_pending_generation(dnd, bot, session),
                    name=f"dnd-generation-retry:{session.chat_id}:{request.get('id')}",
                )
        return restored

    dnd.restore_dnd_sessions = restore_dnd_sessions
    dnd._upupa_dnd_result_recovery_configured = True


__all__ = [
    "RESULT_PHASE_READY",
    "RESULT_PHASE_APPLYING",
    "EFFECT_IN_FLIGHT",
    "EFFECT_DONE",
    "_DurableBotProxy",
    "_snapshot_parse_state",
    "_restore_parse_state",
    "_resume_pending_generation",
    "_resume_pending_result",
    "retry_pending_recovery",
    "reserve_generation_request",
    "transition_to_generation_request",
    "continue_pending_generation",
    "configure_dnd_result_recovery",
]
