import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401
from AI import dnd, dnd_settings as settings
from AI.dnd_adventure import adventure_pacing
from AI.dnd_turn_contract import install_turn_contract_guard, repair_known_roll
from AI.dnd_inventory_reliability import _confirmed_bottle_tag, _should_audit
from core.json_repository import JsonFileRepository


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "_repository", JsonFileRepository(tmp_path / "settings.json"))
    monkeypatch.setattr(settings, "_settings", dict(settings.DEFAULTS))


def test_settings_survive_restart_and_reject_invalid_values(monkeypatch):
    settings._repository.save({"turn_seconds": 60, "personal_seconds": 120, "personal": -2})
    monkeypatch.setattr(settings, "_settings", None)
    values = settings.settings_for()
    assert values["turn_seconds"] == 60
    assert values["personal_seconds"] == 120
    assert values["personal"] == 4


def test_scope_does_not_consume_other_chats():
    from aiogram.dispatcher.event.bases import UNHANDLED
    from aiogram.types import Message
    message = Message.model_validate({"message_id": 1, "date": 0, "chat": {"id": -2, "type": "group"}, "text": "герой"})
    handler = AsyncMock()
    assert asyncio.run(settings.ScopeMiddleware()(handler, message, {})) is UNHANDLED
    handler.assert_not_awaited()


def test_personal_timeout_skips_only_same_unanswered_turn(monkeypatch):
    from AI import dnd_turn_control
    game = SimpleNamespace(chat_id=-1, state="WAITING_ACTION", action_prompt_message_id=5,
                           action_deadline=0, pending_actions={}, action_target_user_ids=[1], starter_user_id=2)
    module = SimpleNamespace(dnd_sessions={-1: game})
    skip = AsyncMock()
    monkeypatch.setattr(dnd_turn_control, "skip_absent_turn", skip)
    asyncio.run(settings.wait_personal_turn(module, object(), game, 5, 0))
    assert skip.await_count == 1
    game.action_prompt_message_id = 6
    asyncio.run(settings.wait_personal_turn(module, object(), game, 5, 0))
    assert skip.await_count == 1
    game.action_prompt_message_id = 5
    game.pending_actions = {"1": {"action": "иду"}}
    asyncio.run(settings.wait_personal_turn(module, object(), game, 5, 0))
    assert skip.await_count == 1


def test_disabled_personal_timeout_schedules_nothing():
    module = SimpleNamespace(_start_background_task=lambda *a, **k: pytest.fail("unexpected timer"))
    settings.schedule_personal_turn(module, object(), SimpleNamespace(action_target_user_ids=[1], action_prompt_message_id=5))


def test_frequency_controls_reach_spotlight_and_monster_direction():
    from AI.dnd_spotlight import enforce_spotlight
    game = SimpleNamespace(mode="participants", scene_count=5, participants={"1": {"user_id": 1, "name": "Аня"}},
                           spotlight_individual_streak=2, recent_scene_types=[], enemy_combatants={})
    settings._settings.update(personal=2, monsters=0, poll=0)
    result, _, _ = enforce_spotlight(game, "Все решают. [ACTION:INPUT]")
    assert "TARGETS" not in result
    assert "первая сюжетная схватка" not in dnd.choose_next_scene_type(game)


def test_short_adventure_enters_finale_and_cannot_loop_forever():
    game = SimpleNamespace(mode="participants", scene_count=11, adventure_length="short", mission_goal="Найти ключ")
    assert "ФИНАЛ СЕЙЧАС" in adventure_pacing(game)
    module = SimpleNamespace(generate_session_response=AsyncMock(return_value="Ещё один выбор. [ACTION:INPUT]"))
    install_turn_contract_guard(module)
    result = asyncio.run(module.generate_session_response(game, "Открываю дверь"))
    assert "[ACTION:END]" in result
    assert "[ACTION:INPUT]" not in result
    assert "MISSION:SUCCESS" not in result


def test_known_skill_does_not_need_another_ai_call_or_player_clarification():
    response = "[ACTION:ROLL;TYPE:CHECK;SKILL:Ловкость рук;REASON:вскрыть замок;DC:9;TARGETS:1]"
    assert ";ABILITY:DEX]" in repair_known_roll(response)
    assert "ABILITY:STR" in repair_known_roll(response.replace(";TYPE:CHECK", ";ABILITY:STR;TYPE:CHECK"))


def test_live_bottle_receipt_recovers_actor_not_next_target():
    game = SimpleNamespace(participants={"1": {"user_id": 1}, "2": {"user_id": 2}}, mode="participants")
    prompt = "Игроки заявили действия одновременно:\n- Игрок: набираю в бутылку машинное масло, перемешанное с зеленой жижей, убираю в инвентарь. (id=1)\nСначала РАЗРЕШИ заявку."
    response = "Набираешь в бутылку мерзкую жижу и запихиваешь в свой инвентарь. [ACTION:INPUT;TARGETS:2]"
    tags = _confirmed_bottle_tag(game, prompt, response)
    assert len(tags) == 1 and "PLAYER:1;" in tags[0]
    assert "машинное масло" in tags[0]
    assert not _confirmed_bottle_tag(game, prompt, "Не " + response)
    assert not _confirmed_bottle_tag(game, prompt.replace("\nСначала", "\n- Другой (id=2)\nСначала"), response)
    assert not _confirmed_bottle_tag(game, prompt, response + "[ITEM:ADD;PLAYER:1;NAME:масло]")


def test_inventory_audit_does_not_spend_quota_on_system_inventory_rules():
    game = SimpleNamespace(mode="participants")
    prompt = "Игроки заявили действия одновременно:\n- Аня: иду домой (id=1)\nСначала РАЗРЕШИ заявку. В инвентаре ложка."
    assert not _should_audit(game, prompt, "Ты дома. [ACTION:INPUT]")


def test_hero_aliases():
    from AI.dnd_state_commands import _STATE_ALIASES
    assert "мой герой" in _STATE_ALIASES["hero"]
    assert "мой инвентарь" in _STATE_ALIASES["inventory"]


def test_restore_suspends_other_chats_without_deleting_records(monkeypatch, tmp_path):
    import json
    path = tmp_path / "state.json"
    record = {"chat_id": -999, "state": "LOBBY", "participants": {"1": {"name": "Игрок"}}}
    path.write_text(json.dumps({"sessions": [record]}), encoding="utf-8")
    monkeypatch.setattr(dnd, "_state_path", lambda: path)
    monkeypatch.setattr(dnd, "dnd_sessions", {})
    monkeypatch.setattr(dnd, "poll_map", {})
    monkeypatch.setattr(dnd, "_suspended_sessions", [])
    monkeypatch.setattr(dnd, "_suspended_state_path", None)
    assert dnd.restore_dnd_sessions(object()) == 0
    assert not dnd.dnd_sessions
    dnd.persist_dnd_sessions()
    assert json.loads(path.read_text(encoding="utf-8"))["sessions"] == [record]


def test_personal_roll_timer_ignores_replaced_roll(monkeypatch):
    from AI import dnd_turn_control
    roll = {"target_user_ids": [1], "personal_deadline": 0}
    game = SimpleNamespace(chat_id=-1, state="WAITING_ROLL", pending_roll=roll, starter_user_id=2)
    module = SimpleNamespace(dnd_sessions={-1: game})
    skip = AsyncMock()
    monkeypatch.setattr(dnd_turn_control, "skip_absent_turn", skip)
    asyncio.run(settings.wait_personal_roll(module, object(), game, roll, 0))
    assert skip.await_count == 1
    game.pending_roll = dict(roll)
    asyncio.run(settings.wait_personal_roll(module, object(), game, roll, 0))
    assert skip.await_count == 1


def test_settings_callback_persists_and_rejects_non_host():
    from aiogram import Router
    router = Router()
    module = SimpleNamespace(dnd_sessions={}, _user_is_host=lambda *args: False)
    settings.configure_dnd_settings(module, router)
    callback = SimpleNamespace(data="dnd:settings:personal", from_user=SimpleNamespace(id=1),
                               bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member"))),
                               answer=AsyncMock(), message=SimpleNamespace(edit_reply_markup=AsyncMock()))
    change = router.callback_query.handlers[-1].callback
    asyncio.run(change(callback))
    assert settings.settings_for()["personal"] == 4
    callback.bot.get_chat_member.return_value.status = "administrator"
    asyncio.run(change(callback))
    assert settings.settings_for()["personal"] == 6
    assert settings._repository.load()["personal"] == 6


def test_broken_poll_with_only_custom_choice_becomes_input():
    from AI.dnd_poll_agency import ensure_group_poll_free_choice
    assert "[ACTION:INPUT]" in ensure_group_poll_free_choice("[ACTION:POLL;OPTIONS:Свой вариант;Другое]")


def test_epilogue_does_not_become_another_forced_finale():
    from AI.dnd_turn_contract import turn_contract
    game = SimpleNamespace(mode="participants", scene_count=12, _upupa_ephemeral_generation_depth=1)
    module = SimpleNamespace(generate_session_response=AsyncMock(return_value="Эпилог."))
    install_turn_contract_guard(module)
    assert asyncio.run(module.generate_session_response(game, "Напиши эпилог")) == "Эпилог."
    assert turn_contract(game) == ""
