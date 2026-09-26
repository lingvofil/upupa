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
from contextlib import contextmanager
from types import SimpleNamespace


RESULT_PHASE_READY = "READY"
RESULT_PHASE_APPLYING = "APPLYING"
EFFECT_IN_FLIGHT = "IN_FLIGHT"
EFFECT_DONE = "DONE"
GROUP_ACTION_REQUEST_KIND = "GROUP_ACTION_CONTINUATION"


class StaleDndSessionError(RuntimeError):
    """A provider response belongs to a session that is no longer current."""


_CORE_SNAPSHOT_FIELDS = (
    "state",
    "last_roll_stat",
    "pending_roll",
    "current_poll_id",
    "pending_poll",
    "last_resolved_poll",
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


def _current_identity(session) -> tuple[str | None, int]:
    campaign_id = str(getattr(session, "campaign_id", "") or "").strip() or None
    try:
        revision = max(0, int(getattr(session, "state_revision", 0) or 0))
    except (TypeError, ValueError):
        revision = 0
    return campaign_id, revision


def _source_identity(session) -> tuple[str | None, int]:
    campaign_id, revision = _current_identity(session)
    try:
        from AI.dnd_event_journal import prospective_revision

        revision = max(revision, int(prospective_revision(session)))
    except Exception:
        # Result recovery must remain usable for legacy/tests even before the
        # journal layer is installed.
        pass
    return campaign_id, revision


def _attach_source_identity(payload: dict, session) -> dict:
    campaign_id, revision = _source_identity(session)
    if campaign_id:
        payload["source_campaign_id"] = campaign_id
    payload["source_revision"] = int(revision)
    return payload


def _identity_mismatch_reason(
    session,
    payload,
    *,
    allow_committed_revision: bool = False,
) -> str | None:
    if not isinstance(payload, dict):
        return None

    source_campaign = str(payload.get("source_campaign_id") or "").strip() or None
    raw_revision = payload.get("source_revision")
    if source_campaign is None and raw_revision is None:
        # Legacy durable requests/results predate Memory v2 transaction identity.
        return None

    current_campaign, current_revision = _current_identity(session)
    if source_campaign is not None and current_campaign != source_campaign:
        return (
            f"campaign-changed:{source_campaign or 'none'}"
            f"->{current_campaign or 'none'}"
        )

    if raw_revision is None:
        return None
    try:
        source_revision = max(0, int(raw_revision))
    except (TypeError, ValueError):
        return "invalid-source-revision"

    allowed = {source_revision}
    if allow_committed_revision:
        # Crash window: the final canonical commit may already be durable while
        # pending_generated_result has not yet been cleared.
        allowed.add(source_revision + 1)
    if current_revision not in allowed:
        return (
            f"revision-changed:{source_revision}"
            f"->{current_revision}"
        )
    return None


def _discard_stale_request(dnd, session, *, reason: str) -> None:
    request = copy.deepcopy(getattr(session, "pending_generation_request", {}) or {})
    request_id = request.get("id")
    rewind_to = request.get("conversation_size")
    if rewind_to is not None and hasattr(dnd, "_rewind_session_conversation"):
        try:
            dnd._rewind_session_conversation(session, rewind_to)
        except Exception:
            logging.exception(
                "DnD stale request conversation rewind failed chat_id=%s request_id=%s",
                getattr(session, "chat_id", None),
                request_id,
            )
    session.pending_generation_request = {}
    logging.warning(
        "DnD discarded stale generation request chat_id=%s request_id=%s reason=%s",
        getattr(session, "chat_id", None),
        request_id,
        reason,
    )
    if getattr(dnd, "dnd_sessions", {}).get(getattr(session, "chat_id", None)) is session:
        dnd.persist_dnd_sessions()


def _discard_stale_result(dnd, session, *, reason: str) -> None:
    result = copy.deepcopy(getattr(session, "pending_generated_result", {}) or {})
    result_id = result.get("id")
    rewind_to = result.get("conversation_size")
    if rewind_to is not None and hasattr(dnd, "_rewind_session_conversation"):
        try:
            dnd._rewind_session_conversation(session, rewind_to)
        except Exception:
            logging.exception(
                "DnD stale result conversation rewind failed chat_id=%s result_id=%s",
                getattr(session, "chat_id", None),
                result_id,
            )
    session.pending_generated_result = {}
    logging.warning(
        "DnD discarded stale generated result chat_id=%s result_id=%s reason=%s",
        getattr(session, "chat_id", None),
        result_id,
        reason,
    )
    if getattr(dnd, "dnd_sessions", {}).get(getattr(session, "chat_id", None)) is session:
        dnd.persist_dnd_sessions()


def _new_generation_request(session, prompt: str) -> dict:
    payload = str(prompt or "")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    conversation = getattr(session, "conversation", None)
    conversation_size = len(conversation) if isinstance(conversation, list) else None
    request = {
        "id": f"gen:{int(time.time() * 1000)}:{digest}",
        "prompt": payload,
        "created_at": time.time(),
        "source_state": str(getattr(session, "state", "") or ""),
        "conversation_size": conversation_size,
    }
    return _attach_source_identity(request, session)


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


def _supersede_stale_generation_request(session, *, replacement_kind: str) -> bool:
    """Drop stale durable bookkeeping before reserving authoritative newer work.

    A request tied to an older campaign revision cannot be retried safely. When
    the current state machine is explicitly reserving a new continuation, the
    current canonical state is the source of truth. Do not rewind conversation
    here: the stale request may be residue from the scene that already produced
    the current WAITING_* state.
    """
    request = getattr(session, "pending_generation_request", {}) or {}
    if not request.get("prompt"):
        return False
    if bool(getattr(session, "_upupa_generation_call_active", False)):
        return False
    stale_reason = _identity_mismatch_reason(session, request)
    if not stale_reason:
        return False
    logging.warning(
        "DnD superseding stale generation request chat_id=%s request_id=%s "
        "reason=%s replacement_kind=%s",
        getattr(session, "chat_id", None),
        request.get("id"),
        stale_reason,
        replacement_kind,
    )
    session.pending_generation_request = {}
    return True


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
    prompt = str(prompt or "")
    existing = _pending_generation_prompt(session)
    if existing:
        if existing == prompt:
            return True
        if not _supersede_stale_generation_request(
            session,
            replacement_kind=str(kind or "GENERATION"),
        ):
            return False
    request = _new_generation_request(session, prompt)
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
    prompt = str(prompt or "")
    existing = _pending_generation_prompt(session)
    if existing:
        if existing == prompt:
            return True
        if not _supersede_stale_generation_request(
            session,
            replacement_kind=str(kind or "GENERATION"),
        ):
            return False
    parent_id = (getattr(session, "pending_generated_result", {}) or {}).get("id")
    request = _new_generation_request(session, prompt)
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
    request = getattr(session, "pending_generation_request", {}) or {}
    result = {
        "id": f"{session.generated_result_seq}:{digest}",
        "text": payload,
        "phase": RESULT_PHASE_READY,
        "created_at": time.time(),
        "source_state": str(getattr(session, "state", "") or ""),
        "telegram_effects": [],
    }
    request_kind = str(request.get("kind") or "")
    request_id = str(request.get("id") or "")
    if request_kind:
        result["source_request_kind"] = request_kind
    if request_id:
        result["source_request_id"] = request_id
    source_prompt = str(request.get("prompt") or "")
    if source_prompt:
        result["source_prompt"] = source_prompt[-6000:]
    if request.get("conversation_size") is not None:
        result["conversation_size"] = request.get("conversation_size")
    if request.get("source_campaign_id"):
        result["source_campaign_id"] = str(request["source_campaign_id"])
    if request.get("source_revision") is not None:
        try:
            result["source_revision"] = max(0, int(request["source_revision"]))
        except (TypeError, ValueError):
            pass
    if "source_revision" not in result:
        _attach_source_identity(result, session)
    return result


@contextmanager
def _parse_request_context(session, request_kind):
    """Restore transient parse semantics from durable request metadata."""
    if str(request_kind or "").upper() != GROUP_ACTION_REQUEST_KIND:
        yield
        return

    existed = hasattr(session, "_upupa_resolving_group_actions")
    previous = getattr(session, "_upupa_resolving_group_actions", None)
    session._upupa_resolving_group_actions = True
    try:
        yield
    finally:
        if existed:
            session._upupa_resolving_group_actions = previous
        else:
            try:
                del session._upupa_resolving_group_actions
            except AttributeError:
                pass


def finalization_state(session, *, create: bool = False) -> dict:
    """Return state tied to the current durable END result.

    It lives inside pending_generated_result so replay keeps it even when the
    normal campaign snapshot is restored to the pre-parse state.
    """
    _ensure(session)
    pending = session.pending_generated_result
    if not isinstance(pending, dict) or not pending.get("text"):
        return {}
    value = pending.get("finalization")
    if isinstance(value, dict):
        return value
    if not create:
        return {}
    result_id = str(pending.get("id") or "unknown")
    result_created_at = pending.get("created_at")
    try:
        result_created_key = format(float(result_created_at), ".17g")
    except (TypeError, ValueError):
        # Legacy/malformed pending result: still create a stable ID for this
        # in-memory replay chain, while current results always have created_at.
        result_created_key = result_id
    value = {
        "completion_id": (
            f"{int(getattr(session, 'chat_id', 0))}:"
            f"{result_created_key}:{result_id}"
        ),
        "created_at": time.time(),
    }
    pending["finalization"] = value
    return value


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


    async def send_photo(self, chat_id, photo, **kwargs):
        effect = self._effect("send_photo")
        if effect.get("status") == EFFECT_DONE:
            return _synthetic_message(effect)
        self._mark_in_flight(effect)
        result = await self._transport.send_photo(chat_id, photo, **kwargs)
        resolved_chat_id = getattr(getattr(result, "chat", None), "id", None)
        self._mark_done(
            effect,
            {
                "message_id": getattr(result, "message_id", None),
                "chat_id": resolved_chat_id if resolved_chat_id is not None else chat_id,
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
    request = getattr(session, "pending_generation_request", {}) or {}
    stale_reason = _identity_mismatch_reason(session, request)
    if stale_reason:
        _discard_stale_request(dnd, session, reason=stale_reason)
        return False
    if not await _deliver_generation_effects(dnd, bot, session):
        return False
    return await _resume_pending_generation(dnd, bot, session)


async def _resume_pending_generation(dnd, bot, session) -> bool:
    _ensure(session)
    prompt = _pending_generation_prompt(session)
    if not prompt:
        return False
    request = getattr(session, "pending_generation_request", {}) or {}
    stale_reason = _identity_mismatch_reason(session, request)
    if stale_reason:
        _discard_stale_request(dnd, session, reason=stale_reason)
        return False
    if bool(getattr(session, "_upupa_generation_call_active", False)):
        return True
    if not await _deliver_generation_effects(dnd, bot, session):
        return False
    request_kind = str(session.pending_generation_request.get("kind") or "")
    request_id = str(session.pending_generation_request.get("id") or "")
    session.state = "RESOLVING"
    dnd.persist_dnd_sessions()
    try:
        response = await dnd.generate_session_response(session, prompt)
        if dnd.dnd_sessions.get(session.chat_id) is not session:
            logging.warning(
                "DnD discarded stale generated continuation chat_id=%s request_id=%s reason=session-replaced",
                getattr(session, "chat_id", None),
                request_id,
            )
            return False
        current_request_id = str(
            (getattr(session, "pending_generation_request", {}) or {}).get("id") or ""
        )
        result_request_id = str(
            (getattr(session, "pending_generated_result", {}) or {}).get("source_request_id") or ""
        )
        if request_id and request_id not in {current_request_id, result_request_id}:
            logging.warning(
                "DnD discarded stale generated continuation chat_id=%s request_id=%s current_request_id=%s result_request_id=%s reason=request-replaced",
                getattr(session, "chat_id", None),
                request_id,
                current_request_id or None,
                result_request_id or None,
            )
            return False
        with _parse_request_context(session, request_kind):
            await dnd.parse_and_execute_turn(bot, session.chat_id, response)
        return True
    except Exception:
        logging.exception(
            "DnD pending generation retry failed chat_id=%s request_id=%s",
            getattr(session, "chat_id", None),
            request_id,
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
    stale_reason = _identity_mismatch_reason(
        session,
        pending,
        allow_committed_revision=phase == RESULT_PHASE_APPLYING,
    )
    if stale_reason:
        _discard_stale_result(dnd, session, reason=stale_reason)
        return

    if phase == RESULT_PHASE_APPLYING:
        # Even if a downstream state such as WAITING_ROLL was already persisted,
        # the process may have died before one of its Telegram sends completed.
        # Restore the local pre-apply snapshot and replay the exact response.
        # Telegram effects already marked DONE are suppressed by _DurableBotProxy.
        snapshot = pending.get("pre_apply_snapshot")
        if _restore_parse_state(session, snapshot, state_policy):
            pending = session.pending_generated_result
            pending["phase"] = RESULT_PHASE_READY
            pending["transaction_open"] = False
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
        with _parse_request_context(session, pending.get("source_request_kind")):
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
        sessions = getattr(dnd, "dnd_sessions", {})
        chat_id = getattr(session, "chat_id", None)
        was_registered = sessions.get(chat_id) is session
        if _generation_is_ephemeral(session):
            result = await original_generate(session, prompt)
            if was_registered and sessions.get(chat_id) is not session:
                raise StaleDndSessionError(
                    f"DnD session changed while auxiliary generation was running: {session.chat_id}"
                )
            return result

        existing = _pending_text(session)
        if existing:
            parse_depth = int(getattr(session, "_upupa_durable_parse_depth", 0) or 0)
            if parse_depth:
                raise RuntimeError(
                    "Nested DnD generation requires transition_to_generation_request()"
                )
            pending_result = getattr(session, "pending_generated_result", {}) or {}
            phase = str(pending_result.get("phase") or RESULT_PHASE_READY).upper()
            stale_reason = _identity_mismatch_reason(
                session,
                pending_result,
                allow_committed_revision=phase == RESULT_PHASE_APPLYING,
            )
            if stale_reason:
                _discard_stale_result(dnd, session, reason=stale_reason)
                raise StaleDndSessionError(
                    f"DnD durable result is stale: {session.chat_id} {stale_reason}"
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

        active_request = getattr(session, "pending_generation_request", {}) or {}
        stale_reason = _identity_mismatch_reason(session, active_request)
        if stale_reason:
            _discard_stale_request(dnd, session, reason=stale_reason)
            raise StaleDndSessionError(
                f"DnD generation request is stale before provider call: "
                f"{session.chat_id} {stale_reason}"
            )

        active_request_id = str(active_request.get("id") or "")
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

        current = sessions.get(chat_id)
        if was_registered and current is not session:
            raise StaleDndSessionError(
                f"DnD session changed while generation was running: {session.chat_id}"
            )
        current_request = getattr(session, "pending_generation_request", {}) or {}
        current_request_id = str(current_request.get("id") or "")
        if (
            was_registered
            and active_request_id
            and current_request_id != active_request_id
        ):
            raise StaleDndSessionError(
                "DnD generation request changed while provider call was running: "
                f"{session.chat_id} {active_request_id} -> {current_request_id or 'none'}"
            )
        stale_reason = _identity_mismatch_reason(session, current_request)
        if stale_reason:
            if current_request_id == active_request_id:
                _discard_stale_request(dnd, session, reason=stale_reason)
            raise StaleDndSessionError(
                f"DnD state revision changed while provider call was running: "
                f"{session.chat_id} {stale_reason}"
            )
        if current is session:
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
        phase = str(pending.get("phase") or RESULT_PHASE_READY).upper()
        stale_reason = _identity_mismatch_reason(
            session,
            pending,
            allow_committed_revision=phase == RESULT_PHASE_APPLYING,
        )
        if stale_reason:
            _discard_stale_result(dnd, session, reason=stale_reason)
            raise StaleDndSessionError(
                f"DnD generated result is stale before apply: {chat_id} {stale_reason}"
            )

        if phase != RESULT_PHASE_APPLYING:
            # Build the snapshot before mutating the durable result. Real
            # GameSession.to_record() runs campaign-state ensure hooks, and some
            # of them normalize pending_generated_result by replacing the dict.
            # If we keep a local reference across to_record(), the APPLYING flag
            # can land on the new dict while pre_apply_snapshot is written to a
            # detached old dict. That produced live
            # applying_result_without_snapshot invariant errors.
            pre_apply_snapshot = _snapshot_parse_state(session)
            pending = session.pending_generated_result
            pending["phase"] = RESULT_PHASE_APPLYING
            pending["pre_apply_snapshot"] = pre_apply_snapshot
            pending["transaction_open"] = True
            pending["transaction_started_at"] = time.time()
            pending.setdefault("telegram_effects", [])
            dnd.persist_dnd_sessions()
        else:
            pending["transaction_open"] = True

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
            pending = current.pending_generated_result
            # Open the single canonical commit boundary only after the exact
            # result has been fully parsed and all durable Telegram effects are
            # accounted for. The event journal then emits at most one revision
            # for the complete logical turn.
            pending["transaction_open"] = False
            pending["transaction_finished_at"] = time.time()
            dnd.persist_dnd_sessions()
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
    "StaleDndSessionError",
    "_DurableBotProxy",
    "_snapshot_parse_state",
    "_restore_parse_state",
    "finalization_state",
    "_resume_pending_generation",
    "_resume_pending_result",
    "retry_pending_recovery",
    "reserve_generation_request",
    "transition_to_generation_request",
    "continue_pending_generation",
    "configure_dnd_result_recovery",
]
