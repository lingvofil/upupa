"""Connect persisted local actions, lifecycle, pause and UI to the DnD router."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

from aiogram import BaseMiddleware

from AI import dnd_turn_lifecycle as lifecycle
from AI.dnd_ai_budget import dnd_turn_budget
from AI.dnd_local_engine import Action, EngineBackend, LocalActionError, execute_action
from AI.dnd_settings import can_edit, schedule_personal_turn, settings_for

_LOCKS = {}
_OWNERS = {}
_PAUSE_COMMANDS = {"днд пауза", "упупа днд пауза"}
_RESUME_COMMANDS = {"днд продолжить", "упупа днд продолжить"}
_READ_COMMANDS = {"днд меню", "упупа днд меню", "днд настройки", "упупа днд настройки"}


@asynccontextmanager
async def game_lock(chat_id):
    """Nested finalization runs in the same task; concurrent effects serialize."""
    task = asyncio.current_task()
    if _OWNERS.get(chat_id) is task:
        yield
        return
    lock = _LOCKS.setdefault(chat_id, asyncio.Lock())
    async with lock:
        _OWNERS[chat_id] = task
        try:
            yield
        finally:
            _OWNERS.pop(chat_id, None)


def _ensure(session):
    session.paused = bool(getattr(session, "paused", False))
    if not isinstance(getattr(session, "pause_state", None), dict):
        session.pause_state = {}
    if not isinstance(getattr(session, "local_delivery_outbox", None), list):
        session.local_delivery_outbox = []
    if not isinstance(getattr(session, "local_enemy_rules", None), dict):
        session.local_enemy_rules = {}
    if not hasattr(session, "local_wait_rule"):
        session.local_wait_rule = None
    if not isinstance(getattr(session, "local_pending_transition", None), dict):
        session.local_pending_transition = {}
    if not isinstance(getattr(session, "local_round_transition", None), dict):
        session.local_round_transition = {}


def configure_dnd_local_state(dnd, *, state_policy, metadata_policy):
    from AI.dnd_local_engine import configure_state_policy
    from AI.dnd_scene_rules import apply_scene_rule_metadata, apply_enemy_rule_metadata, apply_wait_rule_metadata

    configure_state_policy(state_policy)
    state_policy.add_ensure_hook(_ensure)
    for name in ("paused", "pause_state", "local_delivery_outbox", "local_enemy_rules", "local_wait_rule", "local_pending_transition", "local_round_transition"):
        state_policy.add_state_field(name, lambda s, key=name: copy.deepcopy(getattr(s, key, None)))
    state_policy.add_restore_hook(lambda s, _data: _ensure(s))
    metadata_policy.add_postprocessor(apply_scene_rule_metadata)
    metadata_policy.add_postprocessor(apply_enemy_rule_metadata)
    metadata_policy.add_postprocessor(apply_wait_rule_metadata)
    metadata_policy.add_postprocessor(_scene_lifecycle_metadata)


def _scene_lifecycle_metadata(session, original_text, cleaned, notices):
    match = re.search(r"\[SCENE:BEGIN;ID:([^;\]]+)\]", str(original_text), re.I)
    if match:
        current = lifecycle.ensure(session).get("scene_id")
        new_id = match.group(1).strip()[:80]
        if new_id and new_id != current:
            lifecycle.tick_event(session, "scene", current)
            lifecycle.new_scene(session, scene_id=new_id)
            session.local_wait_rule = None
    return cleaned, notices


def _has_combat_foes(session):
    policies = getattr(session, "local_enemy_rules", {}) or {}
    return any(int(enemy.get("hp", 0)) > 0 and enemy.get("status") != "dead"
               and not (policies.get(key) or {}).get("retreated")
               for key, enemy in (getattr(session, "enemy_combatants", {}) or {}).items())


def identity_for(session):
    # Rendering uses only an existing identity. Window synchronization is a
    # persistence/transition operation, never a side effect of opening a menu.
    window = (getattr(session, lifecycle.STATE_FIELD, {}) or {}).get("window") or {}
    return {"campaign_id": str(getattr(session, "campaign_id", "") or ""),
            "turn_id": str(window.get("turn_id") or "setup"),
            "phase": str(window.get("phase") or getattr(session, "state", "")),
            "expected_revision": int(getattr(session, "state_revision", 0) or 0)}


def pause_session(session, *, now=None):
    _ensure(session)
    if session.paused:
        return False
    now = time.time() if now is None else now
    timers = {}
    for key, container in (("action", session), ("roll", getattr(session, "pending_roll", None)),
                           ("poll", getattr(session, "pending_poll", None))):
        name = {"action": "action_deadline", "roll": "personal_deadline", "poll": "deadline"}[key]
        deadline = getattr(container, name, None) if key == "action" else (container or {}).get(name)
        if deadline is not None:
            timers[key] = max(0, float(deadline) - now)
            if key == "action":
                session.action_deadline = None
            else:
                container[name] = None
    session.pause_state = {"remaining": timers, "paused_at": now}
    session.paused = True
    return True


def resume_session(session, *, now=None):
    _ensure(session)
    if not session.paused:
        return False
    now = time.time() if now is None else now
    timers = session.pause_state.get("remaining") or {}
    if "action" in timers:
        session.action_deadline = now + timers["action"]
    if "roll" in timers and getattr(session, "pending_roll", None):
        session.pending_roll["personal_deadline"] = now + timers["roll"]
    if "poll" in timers and getattr(session, "pending_poll", None):
        session.pending_poll["deadline"] = now + timers["poll"]
    session.paused = False
    session.pause_state = {}
    return True


def restart_timers(dnd, bot, session):
    if getattr(session, "paused", False):
        return
    if session.state == "WAITING_ACTION" and getattr(session, "action_deadline", None) is not None and getattr(session, "pending_actions", None):
        dnd._start_background_task(dnd.wait_for_action_timeout(bot, session.chat_id, int(session.action_prompt_message_id)),
                                   name=f"dnd-actions:{session.chat_id}:resume")
    elif session.state == "WAITING_POLL" and getattr(session, "pending_poll", None):
        poll = session.pending_poll
        if poll.get("deadline") is not None:
            dnd._start_background_task(dnd.wait_for_poll_timeout(bot, session.chat_id, int(poll.get("poll_chat_id", session.chat_id)),
                int(poll["message_id"]), list(poll["options"]), str(session.current_poll_id)), name=f"dnd-poll:{session.chat_id}:resume")
    else:
        schedule_personal_turn(dnd, bot, session)


async def flush_local_outbox(dnd, bot, session):
    """Retry exact committed results after a Telegram error, never reroll."""
    _ensure(session)
    while session.local_delivery_outbox:
        row = session.local_delivery_outbox[0]
        await bot.send_message(session.chat_id, row["text"], parse_mode=None)
        session.local_delivery_outbox.pop(0)
        dnd.persist_dnd_sessions()


def _message(bot, session, actor_id, text):
    async def answer(value, **kwargs):
        return await bot.send_message(session.chat_id, value, **kwargs)
    player = (getattr(session, "participants", {}) or {}).get(str(actor_id)) or {}
    return SimpleNamespace(bot=bot, chat=SimpleNamespace(id=session.chat_id),
        from_user=SimpleNamespace(id=actor_id, first_name=player.get("name") or "Игрок"),
        text=text, caption=None, message_id=0, answer=answer)


class LocalRuntime:
    def __init__(self, dnd):
        self.dnd = dnd
        self.backend = EngineBackend.from_runtime(dnd)

    async def execute(self, bot, chat_id, actor_id, payload):
        async with game_lock(chat_id):
            session = self.dnd.dnd_sessions.get(chat_id)
            if session is None:
                return
            _ensure(session)
            kind = str(payload.get("kind") or "").upper()
            if kind == "CHOOSE_ARCHETYPE":
                from AI.dnd_character_templates import choose_archetype

                if session.paused:
                    return
                try:
                    stats = choose_archetype(session, actor_id, payload.get("choice"))
                    self.dnd.persist_dnd_sessions()
                    await bot.send_message(chat_id, "Характеристики роли: " + ", ".join(f"{key} {value}" for key, value in stats.items()))
                except ValueError as error:
                    await bot.send_message(chat_id, str(error))
                return
            if kind in {"PAUSE", "RESUME", "RETRY"}:
                if not self.dnd._user_is_host(session, actor_id):
                    return
                if kind == "PAUSE":
                    pause_session(session)
                elif kind == "RESUME":
                    resume_session(session)
                self.dnd.persist_dnd_sessions()
                if kind == "RESUME":
                    restart_timers(self.dnd, bot, session)
                    self.restart_narration(bot, session)
                elif kind == "RETRY" and not session.paused:
                    from AI.dnd_result_recovery import retry_pending_recovery
                    await flush_local_outbox(self.dnd, bot, session)
                    if await self.complete_transition(bot, session):
                        return
                    with dnd_turn_budget(session, turn_id=lifecycle.current_turn_id(session)):
                        await retry_pending_recovery(self.dnd, bot, session)
                return
            if session.paused:
                await bot.send_message(chat_id, "Партия на паузе. Меню доступно; ход продолжит ведущий.")
                return
            if kind == "FREE_TEXT":
                current = identity_for(session)
                if any(payload.get(key) != current[key] for key in ("campaign_id", "turn_id", "phase")):
                    await bot.send_message(chat_id, "Этот ход уже изменился. Открой меню заново.")
                    return
                window = (getattr(session, lifecycle.STATE_FIELD, {}) or {}).get("window") or {}
                if (session.state != "WAITING_ACTION" or int(actor_id) in (window.get("resolved_actors") or [])
                        or not self.dnd._can_user_act(session, actor_id, getattr(session, "action_target_user_ids", []))
                        or getattr(session, "pending_generation_request", None) or getattr(session, "pending_generated_result", None)):
                    await bot.send_message(chat_id, "Твой ход уже разрешён или ожидает продолжения мастера.")
                    return
                await self.dnd.handle_free_action(_message(bot, session, actor_id, str(payload.get("text") or "")))
                await self.maybe_finalize(bot, session)
                return
            pending = getattr(session, "pending_roll", None) or {}
            if kind == "ROLL" and pending.get("type") in {"ATTACK", "CINEMATIC_ATTACK"}:
                kind = pending["type"]
            inputs = dict(payload.get("inputs") or {})
            inputs.update({key: payload[key] for key in ("item_index", "item_name", "item_fingerprint", "quantity", "rule_id", "declaration") if key in payload})
            try:
                action = Action(str(payload["operation_id"]), str(payload["campaign_id"]), str(payload["turn_id"]),
                    str(payload["phase"]), int(payload["expected_revision"]), int(actor_id), kind,
                    target_id=payload.get("target_id"), item_id=payload.get("item_id"), object_id=payload.get("object_id"), inputs=inputs)
                result = execute_action(session, action, backend=self.backend, ai_mode=settings_for(session)["ai_mode"])
            except (LocalActionError, KeyError, TypeError, ValueError) as error:
                await bot.send_message(chat_id, f"Действие не выполнено: {error}", parse_mode=None)
                return
            if result.replayed:
                if session.local_delivery_outbox:
                    await flush_local_outbox(self.dnd, bot, session)
                else:
                    await bot.send_message(chat_id, result.text, parse_mode=None)
                await self.complete_transition(bot, session)
                return
            session.local_delivery_outbox.append({"operation_id": result.operation_id, "text": result.text})
            if result.round_complete:
                session.local_round_transition = {"round_id": lifecycle.ensure(session).get("round_id"), "operation_id": result.operation_id}
                for npc_result in self._resolve_simple_enemies(session, result.operation_id):
                    session.local_delivery_outbox.append({"operation_id": npc_result.operation_id, "text": npc_result.text})
            if result.needs_narrator:
                from AI.dnd_result_recovery import reserve_generation_request
                summaries = "\n".join((row.get("result") or {}).get("text", "") for row in
                    getattr(session, "dnd_local_engine_v1", {}).get("action_records", [])[-8:])
                prompt = result.continuation_prompt or (
                    "Локальный движок уже применил последствия. Не повторяй кубики, урон или расход. "
                    "Продолжи сцену по этим подтверждённым результатам и сохрани свободу следующего действия:\n" + summaries)
                reserve_generation_request(session, self.dnd.with_scene_direction(session, prompt), kind="LOCAL_ACTION_CONTINUATION")
            elif result.window_closed:
                session.local_pending_transition = {"operation_id": result.operation_id, "campaign_id": action.campaign_id,
                    "turn_id": action.turn_id, "next_actor_id": result.next_actor_id, "round_complete": result.round_complete, "status": "pending"}
            self.dnd.persist_dnd_sessions()
            await flush_local_outbox(self.dnd, bot, session)
            if result.needs_narrator:
                request_id = session.pending_generation_request.get("id")
                self.dnd._start_background_task(self._continue_local(bot, session, request_id, action.turn_id),
                    name=f"dnd-local-narration:{chat_id}:{result.operation_id}")
            elif result.window_closed:
                await self.complete_transition(bot, session)
            else:
                await self.maybe_finalize(bot, session)

    async def complete_transition(self, bot, session):
        transition = getattr(session, "local_pending_transition", {}) or {}
        if not transition:
            return False
        if transition.get("campaign_id") != str(getattr(session, "campaign_id", "")):
            session.local_pending_transition = {}
            self.dnd.persist_dnd_sessions()
            return False
        if transition.get("status") == "pending":
            transition["status"] = "started"
            if transition.get("round_complete"):
                lifecycle.start_combat_round(session)
            self.dnd.persist_dnd_sessions()
            targets = [transition["next_actor_id"]] if transition.get("next_actor_id") else []
            await self.dnd.open_action_window(bot, session.chat_id, target_user_ids=targets)
        elif session.state == "WAITING_ACTION" and not getattr(session, "action_prompt_message_id", None):
            await self.dnd._restore_action_prompt(bot, session.chat_id)
        elif session.state == "RESOLVING":
            # Restart interrupted between reserving the transition and setting
            # WAITING_ACTION. No dice or resource effects run in this recovery.
            targets = [transition["next_actor_id"]] if transition.get("next_actor_id") else []
            await self.dnd.open_action_window(bot, session.chat_id, target_user_ids=targets)
        session.local_pending_transition = {}
        self.dnd.persist_dnd_sessions()
        return True

    async def maybe_finalize(self, bot, session):
        from AI.dnd_completion import DndParticipantCompletionMiddleware

        router = getattr(self.dnd, "dnd_router", None)
        policy = getattr(router, "_upupa_dnd_completion_policy", None)
        completion = DndParticipantCompletionMiddleware(policy)
        await completion._maybe_finalize_action(self.dnd, bot, session.chat_id)

    def restart_narration(self, bot, session):
        request = getattr(session, "pending_generation_request", {}) or {}
        if not session.paused and (request or getattr(session, "pending_generated_result", None)
                or getattr(session, "local_delivery_outbox", None) or getattr(session, "local_pending_transition", None)):
            self.dnd._start_background_task(self._resume_local_work(bot, session), name=f"dnd-local-work:{session.chat_id}:resume")

    async def _resume_local_work(self, bot, session):
        from AI.dnd_result_recovery import retry_pending_recovery

        async with game_lock(session.chat_id):
            if self.dnd.dnd_sessions.get(session.chat_id) is not session or session.paused:
                return
            await flush_local_outbox(self.dnd, bot, session)
            if await self.complete_transition(bot, session):
                return
            with dnd_turn_budget(session, turn_id=lifecycle.current_turn_id(session)):
                await retry_pending_recovery(self.dnd, bot, session)

    async def _continue_local(self, bot, session, request_id, turn_id):
        from AI.dnd_result_recovery import continue_pending_generation

        async with game_lock(session.chat_id):
            if (self.dnd.dnd_sessions.get(session.chat_id) is not session or session.paused
                    or session.pending_generation_request.get("id") != request_id):
                return
            with dnd_turn_budget(session, turn_id=turn_id):
                completed = await continue_pending_generation(self.dnd, bot, session)
            if not completed:
                await bot.send_message(session.chat_id, "Результат сохранён. Ведущий может написать «днд дальше» для продолжения без нового броска.")

    def _resolve_simple_enemies(self, session, operation_id):
        from AI.dnd_local_engine import execute_enemy_action

        results = []
        for enemy_id in list(getattr(session, "local_enemy_rules", {}) or {}):
            try:
                result = execute_enemy_action(session, enemy_id,
                    operation_id=f"{operation_id}:enemy:{enemy_id}", backend=self.backend)
            except LocalActionError:
                continue  # pending reaction, complex NPC, or already acted
            if not result.replayed:
                results.append(result)
        return results


class GameUpdateMiddleware(BaseMiddleware):
    def __init__(self, runtime):
        self.runtime = runtime

    async def __call__(self, handler, event, data):
        dnd = self.runtime.dnd
        message = event if getattr(event, "chat", None) else getattr(event, "message", None)
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        if chat_id is None and getattr(event, "poll_id", None):
            chat_id = dnd.poll_map.get(event.poll_id)
        if chat_id is None:
            return await handler(event, data)
        text = " ".join(str(getattr(event, "text", "") or "").casefold().split())
        session = dnd.dnd_sessions.get(chat_id)
        if text in _READ_COMMANDS:
            return await handler(event, data)
        callback_data = str(getattr(event, "data", "") or "")
        if session and callback_data.startswith("dnd:m:"):
            token = (getattr(session, "menu_ui_state", {}) or {}).get("tokens", {}).get(callback_data.removeprefix("dnd:m:")) or {}
            if token.get("operation") in {"nav", "object"}:
                return await handler(event, data)
        relevant = (text.startswith(("днд", "упупа днд")) or text in {"кидаю", "дальше", "лечить"}
                    or bool(getattr(event, "reply_to_message", None)) or callback_data.startswith("dnd:")
                    or bool(getattr(event, "poll_id", None)))
        if not relevant:
            return await handler(event, data)
        async with game_lock(chat_id):
            session = dnd.dnd_sessions.get(chat_id)
            if session and text in _PAUSE_COMMANDS | _RESUME_COMMANDS:
                if not await can_edit(dnd, event):
                    await event.answer("Пауза доступна ведущему или администратору.")
                    return
                if text in _PAUSE_COMMANDS:
                    pause_session(session)
                else:
                    resume_session(session)
                dnd.persist_dnd_sessions()
                restart_timers(dnd, event.bot, session)
                self.runtime.restart_narration(event.bot, session)
                await event.answer("⏸ Партия на паузе." if session.paused else "▶️ Партия продолжена.")
                return
            if session and text.startswith("днд роль "):
                from AI.dnd_character_templates import choose_archetype
                try:
                    stats = choose_archetype(session, event.from_user.id, text.removeprefix("днд роль "))
                    dnd.persist_dnd_sessions()
                    await event.answer("Характеристики роли: " + ", ".join(f"{key} {value}" for key, value in stats.items()))
                except ValueError as error:
                    await event.answer(str(error))
                return
            if session and getattr(session, "paused", False) and not callback_data.startswith(("dnd:m:", "dnd:settings:")):
                if getattr(event, "poll_id", None):
                    return
                await event.answer("Партия на паузе; «днд меню» доступно.")
                return
            if session and text in {"днд дальше", "дальше"} and (
                    text == "днд дальше" or getattr(session, "local_delivery_outbox", None) or getattr(session, "local_pending_transition", None)):
                if not dnd._user_is_host(session, event.from_user.id):
                    await event.answer("Продолжение восстанавливает ведущий.")
                    return
                await flush_local_outbox(dnd, event.bot, session)
                if await self.runtime.complete_transition(event.bot, session):
                    return
                event = _message(event.bot, session, event.from_user.id, "дальше")
                with dnd_turn_budget(session, turn_id=lifecycle.current_turn_id(session)):
                    return await dnd.handle_dnd_next(event)
            if session and text == "кидаю" and session.state == "WAITING_ROLL" and dnd._is_participant_mode(session):
                pending = getattr(session, "pending_roll", None) or {}
                from AI.dnd_combat import _ability_for_roll

                if pending.get("type") in {"ATTACK", "CINEMATIC_ATTACK"} or (
                        isinstance(pending.get("dc"), int) and _ability_for_roll(pending)):
                    identity = identity_for(session)
                    await self.runtime.execute(event.bot, chat_id, event.from_user.id,
                        {**identity, "kind": "ROLL", "operation_id": f"text-roll:{identity['turn_id']}:{event.from_user.id}"})
                    return
            with dnd_turn_budget(session, turn_id=lifecycle.current_turn_id(session) if session else None):
                return await handler(event, data)


def configure_dnd_local_runtime(dnd=None, router=None):
    if dnd is None:
        from AI import dnd
    router = router or dnd.dnd_router
    if getattr(router, "_upupa_dnd_local_runtime", None):
        return router._upupa_dnd_local_runtime
    runtime = LocalRuntime(dnd)
    from AI.dnd_menu import configure_dnd_menu

    menu_service = configure_dnd_menu(dnd, router, state_policy=router._upupa_dnd_campaign_state_policy,
                                      execute_action=runtime.execute, identity_for=identity_for)
    guard = GameUpdateMiddleware(runtime)
    # Install before collectors/consent middlewares so pause and serialization
    # guard their mutations too. Read-only cards remain available on pause.
    for observer in (router.message, router.callback_query, router.poll_answer):
        observer.outer_middleware._middlewares.insert(0, guard)
    original_persist = dnd.persist_dnd_sessions

    def persist():
        for session in dnd.dnd_sessions.values():
            lifecycle.sync_window(session)
        return original_persist()

    dnd.persist_dnd_sessions = persist

    original_open_action_window = dnd.open_action_window

    async def open_action_window(bot, chat_id, target_user_ids=None):
        prompt = await original_open_action_window(bot, chat_id, target_user_ids=target_user_ids)
        session = dnd.dnd_sessions.get(chat_id)
        if session is not None and menu_service is not None:
            try:
                await menu_service.show_turn_cards(bot, session)
            except Exception:
                logging.exception("DnD automatic turn card failed chat_id=%s", chat_id)
        return prompt

    dnd.open_action_window = open_action_window
    original_direction = dnd.with_scene_direction

    def direction(session, prompt):
        window = (getattr(session, lifecycle.STATE_FIELD, {}) or {}).get("window") or {}
        records = (getattr(session, "dnd_local_engine_v1", {}) or {}).get("action_records", [])
        known = [str(record.get("public_summary") or "") for record in records
                 if record.get("turn_id") == window.get("turn_id") and record.get("public_summary")]
        if known:
            prompt += "\nУЖЕ ИСПОЛНЕНО КОДОМ В ЭТОМ ХОДЕ (не повторять эффекты, кубики или расход):\n" + "\n".join(known)
            remaining = set(window.get("actors", [])) - set(window.get("resolved_actors", []))
            if remaining:
                prompt += "\nНеразрешённые участники этого окна: " + ",".join(str(uid) for uid in sorted(remaining)) + ". Разреши только их новые заявки; уже выполненные действия учитывай как факты."
        return original_direction(session, prompt)

    dnd.with_scene_direction = direction
    original_parse = dnd.parse_and_execute_turn

    async def parse(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        before = lifecycle.current_turn_id(session) if session else None
        pending_id = (getattr(session, "pending_generated_result", None) or {}).get("id") if session else None
        narration_id = str(pending_id or f"{before}:{hashlib.sha256(str(response).encode()).hexdigest()[:24]}")
        prior_seq = (getattr(session, lifecycle.STATE_FIELD, {}) or {}).get("narration_seq", 0) if session else 0
        result = await original_parse(bot, chat_id, response)
        session = dnd.dnd_sessions.get(chat_id)
        if session:
            row = lifecycle.ensure(session)
            if lifecycle.tick_event(session, "narration", narration_id):
                lifecycle.ensure(session)["narration_seq"] = max(row["narration_seq"], prior_seq + 1)
            lifecycle.sync_window(session)
            after = lifecycle.current_turn_id(session)
            if before and before != after:
                lifecycle.tick_event(session, "window", before)
            transition = getattr(session, "local_round_transition", {}) or {}
            row = lifecycle.ensure(session)
            if transition and transition.get("round_id") == row.get("round_id"):
                if _has_combat_foes(session):
                    lifecycle.start_combat_round(session)
                session.local_round_transition = {}
            elif _has_combat_foes(session) and row.get("round_id") is None:
                lifecycle.start_combat_round(session)
            if not _has_combat_foes(session):
                row = lifecycle.ensure(session)
                row.update(round_id=None, round_number=0, round_acted=[])
            dnd.persist_dnd_sessions()
            if getattr(session, "state", None) == "WAITING_ROLL" and menu_service is not None:
                try:
                    await menu_service.show_turn_cards(bot, session)
                except Exception:
                    logging.exception("DnD automatic roll card failed chat_id=%s", chat_id)
        return result

    dnd.parse_and_execute_turn = parse
    from AI import dnd_turn_control
    original_skip = dnd_turn_control.skip_absent_turn

    async def skip(dnd_module, bot, chat_id, requester_user_id, *, automatic=False):
        async with game_lock(chat_id):
            return await original_skip(dnd_module, bot, chat_id, requester_user_id, automatic=automatic)

    dnd_turn_control.skip_absent_turn = skip
    for name in ("finalize_group_actions", "finalize_poll"):
        original = getattr(dnd, name)

        async def finalize(bot, chat_id, *args, _original=original, **kwargs):
            async with game_lock(chat_id):
                session = dnd.dnd_sessions.get(chat_id)
                if not session or getattr(session, "paused", False):
                    return
                with dnd_turn_budget(session, turn_id=lifecycle.current_turn_id(session)):
                    return await _original(bot, chat_id, *args, **kwargs)

        setattr(dnd, name, finalize)
    original_restore = dnd.restore_dnd_sessions

    def restore(bot):
        restored = original_restore(bot)
        for session in dnd.dnd_sessions.values():
            _ensure(session)
            if not session.paused and (session.local_delivery_outbox or session.local_pending_transition):
                runtime.restart_narration(bot, session)
        return restored

    dnd.restore_dnd_sessions = restore
    router._upupa_dnd_local_runtime = runtime
    return runtime
