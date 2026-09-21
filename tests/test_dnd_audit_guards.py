import asyncio
from types import SimpleNamespace

from AI import dnd
from AI import dnd_pacing
from AI import dnd_result_recovery as recovery


def test_world_variety_rules_prefer_open_spaces_and_deprioritize_cramped_defaults():
    rules = dnd_pacing.WORLD_VARIETY_RULES.casefold()

    assert "двух из трёх" in rules
    assert "под открытым небом" in rules
    assert "вентиляц" in rules
    assert "шатры" in rules
    assert "не делай" in rules



def test_scene_momentum_rules_include_breathers_and_exploration():
    rules = dnd_pacing.SCENE_MOMENTUM_RULES.casefold()

    assert "2–3 предыдущими" in rules
    assert "не обязана добавлять новую угрозу" in rules
    assert "спокойная прогулка" in rules
    assert "разговор с npc" in rules
    assert "после напряжённого эпизода" in rules
    assert "низким давлением" in rules
    assert "не превращай исследовательское действие" in rules
    assert "ем яблоко" in rules
    assert "роботы" in rules
    assert "дроны" in rules


def test_opening_rules_give_every_hero_one_free_decision():
    rules = dnd_pacing.OPENING_EXPLORATION_RULES.casefold()

    assert "общим action:input без targets" in rules
    assert "каждый герой один раз решил" in rules
    assert "учти действие каждого героя" in rules
    assert "не запускай голосование" in rules


def test_story_start_action_is_forced_to_group_input():
    source = (
        "На ярмарке уже начинается драка. "
        "[ACTION:POLL;TARGETS:1,2;OPTIONS:Бежать;Драться]"
    )

    guarded = dnd_pacing._force_story_start_group_input(source)

    assert guarded.endswith("[ACTION:INPUT]")
    assert "ACTION:POLL" not in guarded
    assert "TARGETS:" not in guarded


def test_recent_scene_context_exposes_three_scenes_for_variety_control():
    session = SimpleNamespace(
        scene_log=[
            "Старая сцена, которую уже не надо учитывать.",
            "Погоня на рынке.",
            "Драка на крыше.",
            "Переправа через порт.",
        ]
    )

    context = dnd_pacing._recent_scene_context(session)

    assert "Старая сцена" not in context
    assert "Погоня на рынке." in context
    assert "Драка на крыше." in context
    assert "Переправа через порт." in context

def test_repeated_poll_guard_requires_same_scene_and_same_options():
    resolved = {
        "scene_text": "На мосту стражник перекрывает путь. Можно спорить или лезть через перила.",
        "options": ["Спорить", "Лезть через перила"],
        "outcome": "Выбор сделан: Спорить",
    }
    repeated = (
        "На мосту стражник всё ещё перекрывает путь. Можно спорить или лезть через перила. "
        "[ACTION:POLL;OPTIONS:Спорить;Лезть через перила]"
    )
    unrelated = (
        "Через час на ярмарке распорядитель просит решить судьбу приза. "
        "[ACTION:POLL;OPTIONS:Спорить;Лезть через перила]"
    )

    assert dnd_pacing.is_repeated_resolved_poll(repeated, resolved) is True
    assert dnd_pacing.is_repeated_resolved_poll(unrelated, resolved) is False


def test_second_repeat_can_be_forced_to_party_input():
    response = "Опять тот же выбор. [ACTION:POLL;OPTIONS:Да;Нет]"

    guarded = dnd_pacing._force_repeat_poll_to_input(response)

    assert "[ACTION:POLL" not in guarded
    assert guarded.endswith("[ACTION:INPUT]")


def test_game_session_persists_last_resolved_poll():
    session = dnd.GameSession(
        -100500,
        "Тестер",
        active_model="groq",
        conversation=[
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "Погнали."},
        ],
    )
    session.last_resolved_poll = {
        "poll_id": "poll-1",
        "scene_text": "Сцена",
        "options": ["А", "Б"],
        "outcome": "Выбор сделан: А",
        "resolved_at": 123.0,
    }

    restored = dnd.GameSession.from_record(session.to_record())

    assert restored.last_resolved_poll == session.last_resolved_poll


def test_next_recovers_missing_action_prompt(monkeypatch):
    chat_id = -100501
    user_id = 42
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ACTION",
        starter_user_id=user_id,
        action_prompt_message_id=None,
        action_target_user_ids=[user_id],
        pending_actions={},
    )
    dnd.dnd_sessions[chat_id] = session
    calls = []

    async def fake_open(bot, current_chat_id, target_user_ids=None):
        calls.append((bot, current_chat_id, list(target_user_ids or [])))
        session.action_prompt_message_id = 777
        return SimpleNamespace(message_id=777)

    monkeypatch.setattr(dnd, "open_action_window", fake_open)

    class Message:
        chat = SimpleNamespace(id=chat_id)
        from_user = SimpleNamespace(id=user_id)
        bot = object()

        async def answer(self, _text):
            raise AssertionError("successful recovery should not emit an error")

    try:
        asyncio.run(dnd.handle_dnd_next(Message()))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == [(Message.bot, chat_id, [user_id])]
    assert session.action_prompt_message_id == 777


def test_pending_generation_does_not_parse_into_replacement_session():
    chat_id = -100502
    old_session = SimpleNamespace(
        chat_id=chat_id,
        state="RESOLVING",
        pending_generation_request={
            "id": "gen:stale",
            "prompt": "старый ход",
            "kind": "GENERATION",
            "telegram_effects": [],
        },
        pending_generated_result={},
        generated_result_seq=0,
    )
    replacement = SimpleNamespace(chat_id=chat_id, state="WAITING_ACTION")
    calls = {"parse": 0}

    async def generate(_session, _prompt):
        fake_dnd.dnd_sessions[chat_id] = replacement
        return "ответ старой игры [ACTION:INPUT]"

    async def parse(_bot, _chat_id, _response):
        calls["parse"] += 1

    fake_dnd = SimpleNamespace(
        dnd_sessions={chat_id: old_session},
        persist_dnd_sessions=lambda: None,
        generate_session_response=generate,
        parse_and_execute_turn=parse,
    )

    completed = asyncio.run(
        recovery._resume_pending_generation(fake_dnd, object(), old_session)
    )

    assert completed is False
    assert calls["parse"] == 0
    assert fake_dnd.dnd_sessions[chat_id] is replacement


def test_pending_generation_does_not_parse_when_request_changes_in_same_session():
    chat_id = -100503
    session = SimpleNamespace(
        chat_id=chat_id,
        state="RESOLVING",
        pending_generation_request={
            "id": "gen:old",
            "prompt": "старый ход",
            "kind": "GENERATION",
            "telegram_effects": [],
        },
        pending_generated_result={},
        generated_result_seq=0,
    )
    calls = {"parse": 0}

    async def generate(current, _prompt):
        current.pending_generation_request = {
            "id": "gen:new",
            "prompt": "новый ход",
            "kind": "GENERATION",
            "telegram_effects": [],
        }
        return "ответ старого запроса [ACTION:INPUT]"

    async def parse(_bot, _chat_id, _response):
        calls["parse"] += 1

    fake_dnd = SimpleNamespace(
        dnd_sessions={chat_id: session},
        persist_dnd_sessions=lambda: None,
        generate_session_response=generate,
        parse_and_execute_turn=parse,
    )

    completed = asyncio.run(
        recovery._resume_pending_generation(fake_dnd, object(), session)
    )

    assert completed is False
    assert calls["parse"] == 0
    assert session.pending_generation_request["id"] == "gen:new"
