import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from AI import dnd_campaign as campaign
from AI.dnd_adventure import adventure_report, apply_mission_result, handle_adventure_message
from AI.dnd_adventure import message_chunks
from AI.dnd_completion import DndCompletionPolicy
from AI.dnd_spotlight import enforce_spotlight, next_spotlight
from AI.dnd_style import _generate_without_consecutive_input


def session():
    value = SimpleNamespace(
        chat_id=-9001, mode="participants", state="WAITING_ACTION", starter_user_id=1,
        participants={"1": {"user_id": 1, "name": "Алина"}, "2": {"user_id": 2, "name": "Детектор"}},
        character_sheets={"1": {"hp": 8, "status": "alive"}, "2": {"hp": 8, "status": "alive"}},
        pending_actions={}, action_target_user_ids=[1], action_prompt_message_id=91,
        action_deadline=None, pending_roll=None,
    )
    campaign._ensure(value)
    return value


def runtime(value):
    return SimpleNamespace(
        dnd_sessions={value.chat_id: value}, persist_dnd_sessions=lambda: None,
        _user_is_host=lambda s, uid: uid == 1,
        _participant_ids=lambda s: {p["user_id"] for p in s.participants.values() if p.get("active", True)},
        open_action_window=AsyncMock(),
    )


def event(text, uid=1, reply=None):
    return SimpleNamespace(chat=SimpleNamespace(id=-9001), from_user=SimpleNamespace(id=uid, first_name="Алина"),
                           text=text, reply_to_message=SimpleNamespace(message_id=reply) if reply else None)


def test_new_plot_inherits_all_items_without_aliasing_or_resurrecting_consumables(monkeypatch):
    value = session()
    inventory = [{"name": "верёвка", "kind": "item", "quantity": 3}]
    monkeypatch.setattr(campaign, "_player_history", lambda *a: {"inventory": inventory, "artifacts": []})
    campaign._apply_heritage(value, 1, continuation=False)
    assert value.inventories["1"] == inventory
    value.inventories["1"][0]["quantity"] = 1
    assert inventory[0]["quantity"] == 3
    value.inventories["1"] = []
    campaign._apply_heritage(value, 1, continuation=True)
    assert value.inventories["1"] == []


def test_adventure_options_survive_restart():
    value = session()
    value.adventure_length = "long"
    value.mission_goal = "Вернуть ребёнка домой"
    value.setup_prompt_message_id = 17
    restored = session()
    campaign._restore_state(restored, campaign._state(value))
    assert restored.adventure_length == "long"
    assert restored.mission_goal == value.mission_goal
    assert restored.setup_prompt_message_id == 17


def test_custom_plot_and_setup_buttons_exist_in_both_modes():
    for abstract in (True, False):
        buttons = [b.callback_data for row in campaign._plot_keyboard(["сюжет"], abstract).inline_keyboard for b in row]
        assert {"dnd:plot:custom", "dnd:plot:short", "dnd:plot:long", "dnd:plot:goal"} <= set(buttons)


def test_goal_reply_requires_host_and_matching_prompt():
    value = session()
    value.state = "WAITING_PLOT"
    value.setup_prompt_message_id = 17
    bot = SimpleNamespace(send_message=AsyncMock())
    dnd = runtime(value)
    assert not asyncio.run(handle_adventure_message(dnd, bot, event("Похитить ключ", uid=2, reply=17), DndCompletionPolicy()))
    assert asyncio.run(handle_adventure_message(dnd, bot, event("Спасти кузнеца", reply=17), DndCompletionPolicy()))
    assert value.mission_goal == "Спасти кузнеца"
    assert value.setup_prompt_message_id is None


def test_leave_and_return_preserve_inventory_and_skip_spotlight():
    value = session()
    value.inventories["1"] = [{"name": "ключ"}]
    bot = SimpleNamespace(send_message=AsyncMock())
    dnd = runtime(value)
    policy = DndCompletionPolicy()
    asyncio.run(handle_adventure_message(dnd, bot, event("ушла"), policy))
    assert value.participants["1"]["active"] is False
    assert next_spotlight(value) == 2
    assert value.inventories["1"] == [{"name": "ключ"}]
    asyncio.run(handle_adventure_message(dnd, bot, event("захожу"), policy))
    assert value.participants["1"]["active"] is True
    assert value.inventories["1"] == [{"name": "ключ"}]


def test_absent_hero_roll_is_cancelled_not_reassigned():
    value = session()
    value.state = "WAITING_ROLL"
    value.pending_roll = {"target_user_ids": [1], "reason": "обыскать сундук"}
    dnd = runtime(value)
    asyncio.run(handle_adventure_message(dnd, SimpleNamespace(send_message=AsyncMock()), event("ушел"), DndCompletionPolicy()))
    assert value.pending_roll is None
    assert value.state == "WAITING_ACTION"
    dnd.open_action_window.assert_awaited_once()


def test_leaving_shared_targeted_window_keeps_other_submissions():
    value = session()
    value.action_target_user_ids = [1, 2]
    value.pending_actions = {"2": {"user_id": 2, "name": "Детектор", "action": "Открыть дверь"}}
    dnd = runtime(value)
    asyncio.run(handle_adventure_message(dnd, SimpleNamespace(send_message=AsyncMock()), event("ушла"), DndCompletionPolicy()))
    assert value.pending_actions["2"]["action"] == "Открыть дверь"
    assert value.action_target_user_ids == [2]
    dnd.open_action_window.assert_not_awaited()


def test_end_report_is_available_without_epilogue_provider():
    value = session()
    value.mission_goal = "Спасти кузнеца"
    value.initial_inventories = {"1": [{"name": "верёвка", "quantity": 2}]}
    value.inventories = {"1": [{"name": "верёвка", "quantity": 3}, {"name": "ключ"}]}
    value.character_sheets["2"].update(hp=0, status="dead")
    clean = apply_mission_result(value, "Кузнец дома. [MISSION:SUCCESS;EVIDENCE:Кузнец вернулся домой.] [ACTION:END]")
    assert "MISSION:" not in clean
    report = adventure_report(value)
    assert "цель достигнута" in report
    assert "Погибли: Детектор" in report
    assert "верёвка ×1" in report and "ключ ×1" in report


def test_end_is_not_automatically_victory():
    value = session()
    apply_mission_result(value, "До свидания. [ACTION:END]")
    assert "цель не достигнута" in adventure_report(value)
    assert "не зафиксировано" in adventure_report(value)


def test_four_personal_inputs_before_group_input():
    value = session()
    for _ in range(4):
        text, actor, changed = enforce_spotlight(value, "Дверь открыта. [ACTION:INPUT]")
        assert changed and actor in {1, 2}
        assert f"TARGETS:{actor}" in text and "что ты делаешь?" in text
    text, actor, changed = enforce_spotlight(value, "Выберите общий маршрут. [ACTION:INPUT]")
    assert not changed and actor is None
    assert text.endswith("[ACTION:INPUT]")


def test_style_keeps_complete_consequences_and_metadata_beyond_old_limit():
    value = SimpleNamespace(conversation=[])
    response = "Подробности сцены. " * 125 + "Алина нашла ключ. [ITEM:ADD;PLAYER:1;NAME:ключ] [ACTION:INPUT;TARGETS:2]"
    generate = AsyncMock(return_value=response)
    actual = asyncio.run(_generate_without_consecutive_input(generate, value, "Алина обыскивает комнату"))
    assert actual == response


def test_dead_hero_cannot_return_by_presence_command():
    value = session()
    value.participants["1"]["active"] = False
    value.character_sheets["1"].update(hp=0, status="dead")
    bot = SimpleNamespace(send_message=AsyncMock())
    asyncio.run(handle_adventure_message(runtime(value), bot, event("захожу"), DndCompletionPolicy()))
    assert not value.participants["1"]["active"]
    assert "погиб" in bot.send_message.call_args.args[1]


def test_long_narrative_is_split_without_losing_words_or_emoji():
    source = "🎭 Подробное последствие решения.\n" * 350
    chunks = message_chunks(source)
    assert "".join(chunks) == source
    assert all(len(chunk.encode("utf-16-le")) // 2 < 4096 for chunk in chunks)
