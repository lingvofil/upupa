"""Offline integration checks for delivery, scheduling and mixed action windows."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from aiogram import Router

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI import dnd_local_engine as engine
from AI import dnd_local_runtime as runtime
from AI import dnd_result_recovery as recovery
from AI import dnd_turn_lifecycle as lifecycle
from AI.dnd_campaign_state import DndCampaignStatePolicy
from AI.dnd_completion import DndParticipantCompletionMiddleware
from tests.test_dnd_local_engine import backend, rule, session as mechanical_session
from tests.test_dnd_result_recovery import FakeStatePolicy, _fake_dnd


class Bot:
    def __init__(self, *, fail_once=False):
        self.fail_once = fail_once
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("Telegram transport unavailable")
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.messages))


def session(*, group=False):
    sess = mechanical_session()
    sess.chat_id = -100907
    sess.starter_user_id = 1
    sess.conversation = []
    sess.action_target_user_ids = [] if group else [1]
    lifecycle.sync_window(sess)
    runtime._ensure(sess)
    return sess


def fake_dnd(sess):
    saved, tasks, prompts = [], [], []

    def persist():
        saved.append(copy.deepcopy(vars(sess)))

    def start_task(coro, *, name):
        tasks.append(name)
        coro.close()

    async def open_window(bot, chat_id, *, target_user_ids):
        prompts.append((chat_id, target_user_ids))
        sess.state = "WAITING_ACTION"
        sess.action_prompt_message_id += 1
        sess.action_target_user_ids = target_user_ids
        lifecycle.sync_window(sess)

    module = SimpleNamespace(dnd_sessions={sess.chat_id: sess},
        persist_dnd_sessions=persist, _start_background_task=start_task,
        restore_dnd_sessions=lambda bot: 1,
        open_action_window=open_window, _user_is_host=lambda s, uid: uid == s.starter_user_id,
        _can_user_act=lambda s, uid, targets: uid in lifecycle.active_actor_ids(s) and (not targets or uid in targets),
        _is_participant_mode=lambda s: True, _participant_ids=lambda s: set(lifecycle.active_actor_ids(s)),
        with_scene_direction=lambda s, text: text)
    return module, saved, tasks, prompts


def runner(sess, *dice):
    module, saved, tasks, prompts = fake_dnd(sess)
    local = runtime.LocalRuntime(module)
    local.backend, commits = backend(*dice)
    return local, module, saved, tasks, prompts, commits


def payload(sess, kind, *, actor=1, operation="action-1", **extra):
    return runtime.identity_for(sess) | {"kind": kind, "operation_id": operation,
                                       "actor_id": actor} | extra


@pytest.mark.parametrize("state,timer,deadline", [
    ("WAITING_ACTION", "action", 140),
    ("WAITING_ROLL", "roll", 170),
    ("WAITING_POLL", "poll", 190),
])
def test_pause_roundtrip_keeps_remaining_duration_and_resume_does_not_count_pause(state, timer, deadline):
    sess = session()
    sess.state = state
    if timer == "action":
        sess.action_deadline = deadline
    elif timer == "roll":
        sess.pending_roll = {"type": "CHECK", "target_user_ids": [1], "personal_deadline": deadline}
    else:
        sess.pending_poll = {"message_id": 10, "deadline": deadline, "options": ["a", "b"]}
    assert runtime.pause_session(sess, now=100)
    assert not runtime.pause_session(sess, now=130)

    policy = DndCampaignStatePolicy(lambda s: None, lambda s: {}, lambda s, data: None)
    metadata = SimpleNamespace(add_postprocessor=lambda processor: None)
    runtime.configure_dnd_local_state(SimpleNamespace(), state_policy=policy, metadata_policy=metadata)
    stored = json.loads(json.dumps(policy.state(sess)))
    restored = session()
    restored.state = state
    restored.pending_roll = copy.deepcopy(sess.pending_roll)
    restored.pending_poll = copy.deepcopy(getattr(sess, "pending_poll", None))
    restored.action_deadline = None
    policy.restore(restored, stored)
    assert restored.paused and restored.pause_state["remaining"][timer] == deadline - 100
    assert runtime.resume_session(restored, now=1000)
    actual = restored.action_deadline if timer == "action" else (
        restored.pending_roll["personal_deadline"] if timer == "roll" else restored.pending_poll["deadline"])
    assert actual == 1000 + deadline - 100
    assert not runtime.resume_session(restored, now=2000)


def test_resume_restarts_only_current_timer_and_pause_never_schedules(monkeypatch):
    sess = session(group=True)
    module, _saved, tasks, _prompts = fake_dnd(sess)
    personal_calls = []
    monkeypatch.setattr(runtime, "schedule_personal_turn", lambda *args: personal_calls.append(args))

    async def no_timeout(*args):
        return None

    module.wait_for_action_timeout = no_timeout
    module.wait_for_poll_timeout = no_timeout
    sess.pending_actions = {"1": {"user_id": 1, "action": "Открываю дверь"}}
    sess.action_deadline = 150
    runtime.pause_session(sess, now=100)
    runtime.restart_timers(module, Bot(), sess)
    assert tasks == [] and personal_calls == []
    runtime.resume_session(sess, now=1000)
    runtime.restart_timers(module, Bot(), sess)
    assert tasks == [f"dnd-actions:{sess.chat_id}:resume"] and personal_calls == []

    sess.state = "WAITING_POLL"
    sess.current_poll_id = "poll-1"
    sess.pending_poll = {"message_id": 20, "deadline": 1200, "options": ["a", "b"]}
    runtime.restart_timers(module, Bot(), sess)
    assert tasks[-1] == f"dnd-poll:{sess.chat_id}:resume"
    sess.state = "WAITING_ROLL"
    runtime.restart_timers(module, Bot(), sess)
    assert len(personal_calls) == 1


def test_failed_local_delivery_is_durable_retry_does_not_reroll_and_opens_next_window():
    sess = session()
    sess.state = "WAITING_ROLL"
    sess.pending_roll = {"type": "CHECK", "ability": "STR", "dc": 12, "target_user_ids": [1],
                         "local_rule": rule()}
    lifecycle.sync_window(sess)
    local, _module, saved, _tasks, prompts, commits = runner(sess, 4)
    action = payload(sess, "ROLL")
    bot = Bot(fail_once=True)

    async def scenario():
        with pytest.raises(RuntimeError, match="Telegram transport"):
            await local.execute(bot, sess.chat_id, 1, action)
        assert saved[-1]["local_delivery_outbox"]
        assert len(saved[-1]["dnd_local_engine_v1"]["action_records"]) == 1
        assert sess.scene_clocks["alarm"]["value"] == 1
        await local.execute(bot, sess.chat_id, 1, action)

    asyncio.run(scenario())
    assert local.backend.rng.calls == 1 and commits == [("CHECK", 1)]
    assert len(bot.messages) == 1
    assert sess.local_delivery_outbox == []
    assert sess.state == "WAITING_ACTION" and len(prompts) == 1


def test_completed_local_actor_counts_towards_group_free_action_completion():
    sess = session(group=True)
    local, module, _saved, _tasks, _prompts, _commits = runner(sess)
    bot = Bot()
    calls = []

    async def finalize(bot, chat_id, message_id):
        calls.append((chat_id, message_id, copy.deepcopy(sess.pending_actions)))

    module.finalize_group_actions = finalize

    async def scenario():
        await local.execute(bot, sess.chat_id, 1, payload(sess, "WAIT"))
        assert lifecycle.current_window(sess)["resolved_actors"] == [1]
        sess.pending_actions["2"] = {"user_id": 2, "name": "Боря", "action": "Спрашиваю капитана о дороге"}
        finished = await DndParticipantCompletionMiddleware()._maybe_finalize_action(module, bot, sess.chat_id)
        assert finished

    asyncio.run(scenario())
    assert calls == [(sess.chat_id, 10, {"2": {"user_id": 2, "name": "Боря", "action": "Спрашиваю капитана о дороге"}})]


def test_menu_free_action_finishes_group_as_soon_as_last_unresolved_actor_submits(monkeypatch):
    sess = session(group=True)
    local, module, _saved, _tasks, _prompts, _commits = runner(sess)
    bot = Bot()
    calls = []

    async def finalize(bot, chat_id, message_id):
        calls.append((chat_id, message_id))

    module.handle_free_action = dnd.handle_free_action
    module.finalize_group_actions = finalize
    monkeypatch.setattr(dnd, "dnd_sessions", module.dnd_sessions)
    monkeypatch.setattr(dnd, "persist_dnd_sessions", module.persist_dnd_sessions)
    monkeypatch.setattr(dnd, "_start_background_task", module._start_background_task)
    monkeypatch.setattr(dnd, "finalize_group_actions", finalize)

    async def scenario():
        await local.execute(bot, sess.chat_id, 1, payload(sess, "WAIT"))
        await local.execute(bot, sess.chat_id, 2, payload(sess, "FREE_TEXT", actor=2, text="Спрашиваю капитана о дороге"))

    asyncio.run(scenario())
    assert calls == [(sess.chat_id, 10)]


def test_simple_enemy_acts_once_and_its_exact_outcome_is_saved_separately():
    sess = session()
    lifecycle.start_combat_round(sess)
    sess.dnd_lifecycle_v1["round_acted"] = [2]
    sess.local_enemy_rules = {"огр": {"policy": "simple", "retreat_below": 0.2}}
    local, _module, _saved, _tasks, _prompts, _commits = runner(sess)
    calls = []

    def attack(draft, declaration):
        calls.append(copy.deepcopy(declaration))
        draft.character_sheets["1"]["hp"] -= 3
        return "Огр нанёс 3 урона.", "Урон уже применён.", False

    local.backend.combat._resolve_enemy_attack = attack
    action = payload(sess, "WAIT", operation="complete-round")
    bot = Bot()

    async def scenario():
        await local.execute(bot, sess.chat_id, 1, action)
        await local.execute(bot, sess.chat_id, 1, action)

    asyncio.run(scenario())
    assert len(calls) == 1 and sess.character_sheets["1"]["hp"] == 11
    replay = engine.execute_enemy_action(sess, "огр", operation_id="complete-round:enemy:огр", backend=local.backend)
    assert replay.replayed and "3 урона" in replay.text
    assert any(event["type"] == "EnemyActionResolved" for event in replay.events)
    assert any(record["operation_id"] == "complete-round:enemy:огр" for record in sess.action_records)
    assert "3 урона" in sess.pending_generation_request["prompt"]


def test_retreat_policy_prevents_enemy_from_acting_again_in_later_round():
    sess = session()
    lifecycle.start_combat_round(sess)
    sess.dnd_lifecycle_v1["round_acted"] = [2]
    sess.enemy_combatants["огр"]["hp"] = 3
    sess.local_enemy_rules = {"огр": {"policy": "simple", "retreat_below": 0.2}}
    local, _module, _saved, _tasks, _prompts, _commits = runner(sess)
    action = engine.make_action(sess, 1, "WAIT", operation_id="complete-round")
    result = engine.execute_action(sess, action, backend=local.backend)
    assert result.round_complete
    enemies = local._resolve_simple_enemies(sess, result.operation_id)
    assert len(enemies) == 1 and "отступает" in enemies[0].text
    lifecycle.start_combat_round(sess)
    assert engine.choose_enemy_action(sess, "огр") is None


def test_paused_saved_request_neither_generates_on_restore_nor_direct_recovery():
    policy = FakeStatePolicy()
    module, sess, calls, _markers, scheduled = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(module, state_policy=policy)
    sess.state = "WAITING_ACTION"
    sess.paused = True
    recovery.reserve_generation_request(sess, "Последствия уже применены.", kind="LOCAL_ACTION_CONTINUATION")
    saved_request = copy.deepcopy(sess.pending_generation_request)
    try:
        assert module.restore_dnd_sessions(Bot()) == 1
        assert not asyncio.run(recovery.continue_pending_generation(module, Bot(), sess))
        assert calls["generate"] == calls["parse"] == 0
        assert not scheduled
        assert sess.pending_generation_request == saved_request
        assert sess.state == "WAITING_ACTION"
    finally:
        for coro, _name in scheduled:
            coro.close()


def test_paused_saved_generated_result_does_not_replay_until_resume():
    policy = FakeStatePolicy()
    module, sess, calls, _markers, scheduled = _fake_dnd(policy)
    recovery.configure_dnd_result_recovery(module, state_policy=policy)
    asyncio.run(module.generate_session_response(sess, "Текущий ход"))
    saved_result = copy.deepcopy(sess.pending_generated_result)
    sess.paused = True
    try:
        assert module.restore_dnd_sessions(Bot()) == 1
        asyncio.run(recovery._resume_pending_result(module, Bot(), sess, policy))
        assert calls["generate"] == 1 and calls["parse"] == 0
        assert not scheduled
        assert sess.pending_generated_result == saved_result
    finally:
        for coro, _name in scheduled:
            coro.close()


@pytest.mark.parametrize("parse_failed", [False, True])
def test_host_retry_replays_saved_narration_without_new_generation_or_duplicate_delivery(parse_failed):
    policy = FakeStatePolicy()
    module, sess, calls, _markers, _scheduled = _fake_dnd(policy)
    sess.starter_user_id = 1
    module._user_is_host = lambda current, uid: uid == current.starter_user_id
    seen = []

    async def parse(bot, chat_id, text):
        calls["parse"] += 1
        seen.append((sess.campaign_marker, text))
        await bot.send_message(chat_id, text)
        if parse_failed and calls["parse"] == 1:
            sess.campaign_marker = "partially-mutated"
            raise RuntimeError("interrupted after narration delivery")
        sess.state = "WAITING_ACTION"

    module.parse_and_execute_turn = parse
    recovery.configure_dnd_result_recovery(module, state_policy=policy)
    local = runtime.LocalRuntime(module)
    bot = Bot()

    async def scenario():
        response = await module.generate_session_response(sess, "Уже применённый исход")
        assert not sess.pending_generation_request and sess.pending_generated_result["text"] == response
        if parse_failed:
            with pytest.raises(RuntimeError, match="interrupted"):
                await module.parse_and_execute_turn(bot, sess.chat_id, response)
            assert sess.pending_generated_result["phase"] == recovery.RESULT_PHASE_APPLYING
        await local.execute(bot, sess.chat_id, 1, {"kind": "RETRY"})
        assert not sess.pending_generated_result and sess.state == "WAITING_ACTION"
        assert calls["generate"] == 1
        assert calls["parse"] == (2 if parse_failed else 1)
        assert seen == [("before", response)] * calls["parse"]
        assert len(bot.messages) == 1 and bot.messages[0][1] == response

    asyncio.run(scenario())


def test_restoring_paused_poll_keeps_mapping_without_timer_or_restore_error(monkeypatch, tmp_path):
    sess = session()
    sess.state = "WAITING_POLL"
    sess.current_poll_id = "paused-poll"
    sess.pending_poll = {"message_id": 20, "deadline": 190, "options": ["a", "b"], "votes": {}}
    runtime.pause_session(sess, now=100)
    saved = tmp_path / "dnd-state.json"
    saved.write_text(json.dumps({"sessions": [{"chat_id": sess.chat_id}]}), encoding="utf-8")
    validated, tasks = [], []

    def start_task(coro, *, name):
        tasks.append(name)
        coro.close()

    monkeypatch.setattr(dnd, "dnd_sessions", {})
    monkeypatch.setattr(dnd, "poll_map", {})
    monkeypatch.setattr(dnd, "ALLOWED_CHAT_ID", sess.chat_id)
    monkeypatch.setattr(dnd, "_state_path", lambda: saved)
    monkeypatch.setattr(dnd.GameSession, "from_record", classmethod(lambda cls, record: sess))
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "_start_background_task", start_task)
    monkeypatch.setattr(dnd, "_validate_dnd_session_state", lambda s, *, boundary: validated.append(boundary))
    monkeypatch.setattr(dnd, "_suspended_state_path", None)
    monkeypatch.setattr(dnd, "_suspended_sessions", [])
    assert dnd.restore_dnd_sessions(Bot()) == 1
    assert validated == ["restore"]
    assert tasks == []
    assert dnd.poll_map["paused-poll"] == sess.chat_id
    assert sess.pending_poll["deadline"] is None and sess.pause_state["remaining"]["poll"] == 90


def test_runtime_open_action_window_automatically_refreshes_turn_cards(monkeypatch):
    from AI import dnd_menu
    from AI import dnd_turn_control

    sess = session()
    module, _saved, _tasks, prompts = fake_dnd(sess)
    router = Router()
    router._upupa_dnd_campaign_state_policy = FakeStatePolicy()
    module.dnd_router = router

    async def parse(_bot, _chat_id, _response):
        return None

    async def no_finalize(*_args, **_kwargs):
        return None

    module.parse_and_execute_turn = parse
    module.finalize_group_actions = no_finalize
    module.finalize_poll = no_finalize
    card_calls = []

    class MenuService:
        async def show_turn_cards(self, _bot, current):
            card_calls.append((current.state, list(current.action_target_user_ids)))

    menu_service = MenuService()
    monkeypatch.setattr(dnd_menu, "configure_dnd_menu", lambda *args, **kwargs: menu_service)
    monkeypatch.setattr(dnd_turn_control, "skip_absent_turn", dnd_turn_control.skip_absent_turn)
    runtime.configure_dnd_local_runtime(module, router)

    asyncio.run(module.open_action_window(Bot(), sess.chat_id, target_user_ids=[2]))

    assert prompts[-1] == (sess.chat_id, [2])
    assert card_calls == [("WAITING_ACTION", [2])]


@pytest.mark.parametrize("enemy_retreats", [False, True])
def test_narration_after_completed_combat_round_starts_next_round_or_finishes_combat(monkeypatch, enemy_retreats):
    from AI import dnd_menu
    from AI import dnd_turn_control

    sess = session()
    sess.local_enemy_rules = {"огр": {"policy": "simple", "retreat_below": 0.2}}
    if enemy_retreats:
        sess.enemy_combatants["огр"]["hp"] = 3
    lifecycle.start_combat_round(sess)
    first_round = sess.dnd_lifecycle_v1["round_id"]
    module, _saved, _tasks, _prompts = fake_dnd(sess)
    router = Router()
    router._upupa_dnd_campaign_state_policy = FakeStatePolicy()
    module.dnd_router = router

    async def no_finalize(*args, **kwargs):
        return None

    async def parse(bot, chat_id, response):
        sess.pending_generation_request = {}
        sess.pending_generated_result = {}
        await module.open_action_window(bot, chat_id, target_user_ids=[1])

    module.parse_and_execute_turn = parse
    module.finalize_group_actions = no_finalize
    module.finalize_poll = no_finalize
    monkeypatch.setattr(dnd_menu, "configure_dnd_menu", lambda *args, **kwargs: None)
    # Runtime installs a module-level skip lock as well as local router hooks.
    # Restore that global surface after this isolated fake composition.
    monkeypatch.setattr(dnd_turn_control, "skip_absent_turn", dnd_turn_control.skip_absent_turn)
    local = runtime.configure_dnd_local_runtime(module, router)
    local.backend, _commits = backend()
    attacks = []

    def attack(draft, declaration):
        attacks.append(copy.deepcopy(declaration))
        return "Огр промахнулся.", "Промах уже подтверждён.", False

    local.backend.combat._resolve_enemy_attack = attack
    bot = Bot()

    async def scenario():
        await local.execute(bot, sess.chat_id, 1, payload(sess, "WAIT", operation="round1-actor1"))
        await local.execute(bot, sess.chat_id, 2, payload(sess, "WAIT", actor=2, operation="round1-actor2"))
        assert len(attacks) == int(not enemy_retreats) and sess.pending_generation_request
        await module.parse_and_execute_turn(bot, sess.chat_id, "Огр промахнулся. Твой ход. [ACTION:INPUT]")
        assert sess.dnd_lifecycle_v1["round_id"] != first_round
        assert sess.dnd_lifecycle_v1["round_number"] == (0 if enemy_retreats else 2)
        assert sess.dnd_lifecycle_v1["round_acted"] == []
        await local.execute(bot, sess.chat_id, 1, payload(sess, "WAIT", operation="round2-actor1"))
        assert len(attacks) == int(not enemy_retreats) and not sess.pending_generation_request
        await local.execute(bot, sess.chat_id, 2, payload(sess, "WAIT", actor=2, operation="round2-actor2"))
        assert len(attacks) == (0 if enemy_retreats else 2)
        if enemy_retreats:
            assert sess.dnd_lifecycle_v1["round_id"] is None
            assert not sess.pending_generation_request

    asyncio.run(scenario())


def test_non_host_next_alias_cannot_recover_or_advance_local_delivery():
    sess = session()
    sess.state = "RESOLVING"
    lifecycle.close_window(sess)
    sess.local_delivery_outbox = [{"operation_id": "done", "text": "Уже подтверждённый исход"}]
    sess.local_pending_transition = {"operation_id": "done", "campaign_id": sess.campaign_id,
        "turn_id": lifecycle.current_turn_id(sess), "next_actor_id": 2,
        "round_complete": False, "status": "pending"}
    local, _module, _saved, _tasks, prompts, _commits = runner(sess)
    bot = Bot()

    async def answer(text, **kwargs):
        await bot.send_message(sess.chat_id, text, **kwargs)

    event = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=sess.chat_id),
        from_user=SimpleNamespace(id=2, first_name="Боря"), text="днд дальше", answer=answer)

    async def handler(event, data):
        raise AssertionError("The recovery command must be consumed by its access guard")

    asyncio.run(runtime.GameUpdateMiddleware(local)(handler, event, {"bot": bot}))
    assert sess.local_delivery_outbox and sess.local_pending_transition["status"] == "pending"
    assert sess.state == "RESOLVING" and not prompts
    assert all("Уже подтверждённый исход" not in row[1] for row in bot.messages)
