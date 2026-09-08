import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.messages))


class FakeMessage:
    def __init__(self, *, chat_id, user_name="Алиса", bot=None):
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=1, first_name=user_name)
        self.bot = bot or FakeBot()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


def test_system_prompt_uses_skill_checks_and_calibrated_dc():
    assert "SKILL:Внимательность" in dnd.DND_SYSTEM_PROMPT
    assert "Расследование" in dnd.DND_SYSTEM_PROMPT
    assert "TYPE:CHECK — основной тип броска" in dnd.DND_SYSTEM_PROMPT
    assert "TYPE:SAVE используй ТОЛЬКО" in dnd.DND_SYSTEM_PROMPT
    assert "DC 18–20 не назначай" in dnd.DND_SYSTEM_PROMPT
    assert "STAT:Название" not in dnd.DND_SYSTEM_PROMPT
    assert "STAT:Телосложение" not in dnd.DND_SYSTEM_PROMPT


def test_parse_roll_command_supports_save_reason_dc_and_disadvantage():
    roll = dnd._parse_roll_command(
        "ROLL;TYPE:SAVE;REASON:не отравиться дымом;DC:14;MODE:DISADVANTAGE"
    )

    assert roll == {
        "type": "SAVE",
        "skill": None,
        "reason": "не отравиться дымом",
        "dc": 14,
        "mode": "DISADVANTAGE",
        "target_user_ids": [],
    }


def test_parse_roll_command_supports_named_check_skills():
    assert dnd._parse_roll_command(
        "ROLL;TYPE:CHECK;SKILL:Внимательность;REASON:заметить следы;DC:10;MODE:NORMAL"
    ) == {
        "type": "CHECK",
        "skill": "Внимательность",
        "reason": "заметить следы",
        "dc": 10,
        "mode": "NORMAL",
        "target_user_ids": [],
    }
    assert dnd._parse_roll_command(
        "ROLL;TYPE:CHECK;SKILL:расследование;REASON:осмотреть замок;DC:12;MODE:NORMAL"
    )["skill"] == "Расследование"


def test_parse_roll_command_supports_targets():
    roll = dnd._parse_roll_command(
        "ROLL;TYPE:SAVE;REASON:увернуться;DC:12;MODE:NORMAL;TARGETS:123,456"
    )

    assert roll["target_user_ids"] == [123, 456]


def test_parse_roll_command_ignores_skill_on_save_and_unknown_skill():
    assert dnd._parse_roll_command(
        "ROLL;TYPE:SAVE;SKILL:Внимательность;REASON:уклониться;DC:11;MODE:NORMAL"
    )["skill"] is None
    assert dnd._parse_roll_command(
        "ROLL;TYPE:CHECK;SKILL:Супернюх;REASON:понюхать;DC:9;MODE:NORMAL"
    )["skill"] is None


def test_parse_roll_command_does_not_use_legacy_characteristic():
    roll = dnd._parse_roll_command("ROLL;STAT:Ловкость")

    assert roll == {
        "type": "CHECK",
        "skill": None,
        "reason": "проверка по ситуации",
        "dc": None,
        "mode": "NORMAL",
        "target_user_ids": [],
    }
    assert "stat" not in roll


def test_parse_roll_command_caps_dc_for_pure_d20_and_accepts_short_modes():
    assert dnd._parse_roll_command(
        "ROLL;TYPE:CHECK;SKILL:Атлетика;REASON:перепрыгнуть провал;DC:99;MODE:ADV"
    ) == {
        "type": "CHECK",
        "skill": "Атлетика",
        "reason": "перепрыгнуть провал",
        "dc": 17,
        "mode": "ADVANTAGE",
        "target_user_ids": [],
    }
    assert dnd._parse_roll_command(
        "ROLL;TYPE:CHECK;REASON:очень сложная попытка;DC:20;MODE:NORMAL"
    )["dc"] == 17
    assert dnd._parse_roll_command(
        "ROLL;TYPE:SAVE;REASON:не упасть;DC:1;MODE:DIS"
    ) == {
        "type": "SAVE",
        "skill": None,
        "reason": "не упасть",
        "dc": 5,
        "mode": "DISADVANTAGE",
        "target_user_ids": [],
    }


def test_roll_d20_uses_highest_for_advantage(monkeypatch):
    values = iter([4, 17])
    monkeypatch.setattr(dnd.random, "randint", lambda _a, _b: next(values))

    rolls, result = dnd._roll_d20("ADVANTAGE")

    assert rolls == [4, 17]
    assert result == 17


def test_roll_d20_uses_lowest_for_disadvantage(monkeypatch):
    values = iter([18, 6])
    monkeypatch.setattr(dnd.random, "randint", lambda _a, _b: next(values))

    rolls, result = dnd._roll_d20("DISADVANTAGE")

    assert rolls == [18, 6]
    assert result == 6


def test_roll_outcome_uses_dc_without_modifiers():
    assert dnd._roll_outcome(14, 14) == "успех"
    assert dnd._roll_outcome(13, 14) == "провал"
    assert dnd._natural_roll_note(20) == "натуральная 20"
    assert dnd._natural_roll_note(1) == "натуральная 1"


def test_parse_turn_stores_story_save_without_skill(monkeypatch):
    chat_id = -100701
    session = SimpleNamespace(
        state="WAITING_ACTION",
        pending_roll=None,
        last_roll_stat="старое значение",
        action_prompt_message_id=55,
        pending_actions={"1": {"action": "что-то"}},
        action_deadline=123.0,
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()

    try:
        asyncio.run(
            dnd.parse_and_execute_turn(
                bot,
                chat_id,
                "[ACTION:ROLL;TYPE:SAVE;REASON:не сорваться с карниза;DC:13;MODE:ADVANTAGE]",
            )
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert session.state == "WAITING_ROLL"
    assert session.pending_roll == {
        "type": "SAVE",
        "skill": None,
        "reason": "не сорваться с карниза",
        "dc": 13,
        "mode": "ADVANTAGE",
        "target_user_ids": [],
    }
    assert session.last_roll_stat is None
    assert session.pending_actions == {}
    assert session.action_prompt_message_id is None
    assert "Спасбросок: не сорваться с карниза" in bot.messages[-1][1]
    assert "сложность 13" in bot.messages[-1][1]
    assert "DC 13" not in bot.messages[-1][1]
    assert "преимущество" in bot.messages[-1][1]


def test_parse_turn_shows_named_skill_check(monkeypatch):
    chat_id = -100707
    session = SimpleNamespace(
        state="WAITING_ACTION",
        pending_roll=None,
        last_roll_stat=None,
        action_prompt_message_id=55,
        pending_actions={},
        action_deadline=None,
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()

    try:
        asyncio.run(
            dnd.parse_and_execute_turn(
                bot,
                chat_id,
                "[ACTION:ROLL;TYPE:CHECK;SKILL:Внимательность;REASON:заметить следы у двери;DC:10;MODE:NORMAL]",
            )
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert session.pending_roll["skill"] == "Внимательность"
    assert "🎲 Внимательность: заметить следы у двери" in bot.messages[-1][1]


def test_handle_roll_reports_success_and_sends_story_context_to_master(monkeypatch):
    chat_id = -100702
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ROLL",
        pending_roll={
            "type": "SAVE",
            "skill": None,
            "reason": "выдержать действие яда",
            "dc": 12,
            "mode": "ADVANTAGE",
        },
        last_roll_stat=None,
        recent_scene_types=[],
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "with_scene_direction", lambda _session, prompt: prompt)
    values = iter([5, 18])
    monkeypatch.setattr(dnd.random, "randint", lambda _a, _b: next(values))

    prompts = []
    parsed = []

    async def fake_generate(_session, prompt):
        prompts.append(prompt)
        return "продолжение [ACTION:INPUT]"

    async def fake_parse(bot, resolved_chat_id, text):
        parsed.append((bot, resolved_chat_id, text))

    monkeypatch.setattr(dnd, "generate_session_response", fake_generate)
    monkeypatch.setattr(dnd, "parse_and_execute_turn", fake_parse)
    message = FakeMessage(chat_id=chat_id)

    try:
        asyncio.run(dnd.handle_roll(message))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert session.state == "RESOLVING"
    assert session.pending_roll is None
    assert message.answers[0][0] == (
        "🎲 Алиса: Спасбросок — выдержать действие яда\n"
        "⚙️ Сложность — 12 (с преимуществом).\n"
        "🎯 Броски кубика — 5 и 18, результат — 18"
    )
    assert "Сложность: 12; результат: успех" in prompts[0]
    assert "Броски d20: [5, 18]; итог: 18" in prompts[0]
    assert parsed == [(message.bot, chat_id, "продолжение [ACTION:INPUT]")]


def test_handle_roll_uses_skill_name_and_story_context(monkeypatch):
    chat_id = -100708
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ROLL",
        pending_roll={
            "type": "CHECK",
            "skill": "Расследование",
            "reason": "понять механизм тайника",
            "dc": 12,
            "mode": "NORMAL",
        },
        last_roll_stat=None,
        recent_scene_types=[],
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "with_scene_direction", lambda _session, prompt: prompt)
    monkeypatch.setattr(dnd.random, "randint", lambda _a, _b: 13)
    prompts = []

    async def fake_generate(_session, prompt):
        prompts.append(prompt)
        return "продолжение [ACTION:INPUT]"

    async def fake_parse(_bot, _chat_id, _text):
        return None

    monkeypatch.setattr(dnd, "generate_session_response", fake_generate)
    monkeypatch.setattr(dnd, "parse_and_execute_turn", fake_parse)
    message = FakeMessage(chat_id=chat_id, user_name="Alina")

    try:
        asyncio.run(dnd.handle_roll(message))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert message.answers[0][0].startswith(
        "🎲 Alina: Расследование — понять механизм тайника\n"
    )
    assert "проверку навыка «Расследование»" in prompts[0]


def test_handle_roll_simplifies_normal_failure_summary(monkeypatch):
    chat_id = -100705
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ROLL",
        pending_roll={
            "type": "SAVE",
            "skill": None,
            "reason": "успеть выбежать из рушащегося здания",
            "dc": 12,
            "mode": "NORMAL",
        },
        last_roll_stat=None,
        recent_scene_types=[],
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd.random, "randint", lambda _a, _b: 2)

    async def fake_generate(_session, _prompt):
        return "продолжение [ACTION:INPUT]"

    async def fake_parse(_bot, _chat_id, _text):
        return None

    monkeypatch.setattr(dnd, "generate_session_response", fake_generate)
    monkeypatch.setattr(dnd, "parse_and_execute_turn", fake_parse)
    message = FakeMessage(chat_id=chat_id, user_name="Alina")

    try:
        asyncio.run(dnd.handle_roll(message))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert message.answers[0][0] == (
        "🎲 Alina: Спасбросок — успеть выбежать из рушащегося здания\n"
        "⚙️ Сложность — 12.\n"
        "🎯 Бросок кубика — 2"
    )
    assert "d20" not in message.answers[0][0]
    assert "DC" not in message.answers[0][0]
    assert "|" not in message.answers[0][0]


def test_handle_roll_labels_disadvantage_in_difficulty_line(monkeypatch):
    chat_id = -100706
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ROLL",
        pending_roll={
            "type": "SAVE",
            "skill": None,
            "reason": "устоять на ногах",
            "dc": 14,
            "mode": "DISADVANTAGE",
        },
        last_roll_stat=None,
        recent_scene_types=[],
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    values = iter([17, 6])
    monkeypatch.setattr(dnd.random, "randint", lambda _a, _b: next(values))

    async def fake_generate(_session, _prompt):
        return "продолжение [ACTION:INPUT]"

    async def fake_parse(_bot, _chat_id, _text):
        return None

    monkeypatch.setattr(dnd, "generate_session_response", fake_generate)
    monkeypatch.setattr(dnd, "parse_and_execute_turn", fake_parse)
    message = FakeMessage(chat_id=chat_id, user_name="Alina")

    try:
        asyncio.run(dnd.handle_roll(message))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert message.answers[0][0] == (
        "🎲 Alina: Спасбросок — устоять на ногах\n"
        "⚙️ Сложность — 14 (с помехой).\n"
        "🎯 Броски кубика — 17 и 6, результат — 6"
    )


def test_old_waiting_roll_state_restores_without_characteristic():
    session = dnd.GameSession.from_record(
        {
            "chat_id": -100703,
            "active_model": "groq",
            "conversation": [{"role": "assistant", "content": "старый контекст"}],
            "state": "WAITING_ROLL",
            "last_roll_stat": "Ловкость",
        }
    )

    assert session.pending_roll == {
        "type": "CHECK",
        "skill": None,
        "reason": "проверка по ситуации",
        "dc": None,
        "mode": "NORMAL",
        "target_user_ids": [],
    }
    assert "stat" not in session.pending_roll


def test_old_pending_roll_with_stat_is_sanitized():
    session = dnd.GameSession.from_record(
        {
            "chat_id": -100704,
            "active_model": "groq",
            "conversation": [{"role": "assistant", "content": "старый контекст"}],
            "state": "WAITING_ROLL",
            "pending_roll": {
                "type": "SAVE",
                "stat": "Телосложение",
                "dc": 15,
                "mode": "DISADVANTAGE",
            },
        }
    )

    assert session.pending_roll == {
        "type": "SAVE",
        "skill": None,
        "reason": "проверка по ситуации",
        "dc": 15,
        "mode": "DISADVANTAGE",
        "target_user_ids": [],
    }
    assert "stat" not in session.pending_roll


def test_pending_skill_check_restores_with_canonical_skill_name():
    session = dnd.GameSession.from_record(
        {
            "chat_id": -100709,
            "active_model": "groq",
            "conversation": [{"role": "assistant", "content": "контекст"}],
            "state": "WAITING_ROLL",
            "pending_roll": {
                "type": "CHECK",
                "skill": "внимательность",
                "reason": "осмотреть комнату",
                "dc": 10,
                "mode": "NORMAL",
            },
        }
    )

    assert session.pending_roll["skill"] == "Внимательность"
