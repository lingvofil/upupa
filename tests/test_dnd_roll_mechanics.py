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


def test_system_prompt_uses_story_reasons_not_characteristics():
    assert "REASON:перепрыгнуть провал" in dnd.DND_SYSTEM_PROMPT
    assert "REASON:не отравиться дымом" in dnd.DND_SYSTEM_PROMPT
    assert "STAT:Название" not in dnd.DND_SYSTEM_PROMPT
    assert "STAT:Телосложение" not in dnd.DND_SYSTEM_PROMPT


def test_parse_roll_command_supports_save_reason_dc_and_disadvantage():
    roll = dnd._parse_roll_command(
        "ROLL;TYPE:SAVE;REASON:не отравиться дымом;DC:14;MODE:DISADVANTAGE"
    )

    assert roll == {
        "type": "SAVE",
        "reason": "не отравиться дымом",
        "dc": 14,
        "mode": "DISADVANTAGE",
    }


def test_parse_roll_command_does_not_use_legacy_characteristic():
    roll = dnd._parse_roll_command("ROLL;STAT:Ловкость")

    assert roll == {
        "type": "CHECK",
        "reason": "проверка по ситуации",
        "dc": None,
        "mode": "NORMAL",
    }
    assert "stat" not in roll


def test_parse_roll_command_clamps_dc_and_accepts_short_mode_aliases():
    assert dnd._parse_roll_command(
        "ROLL;TYPE:CHECK;REASON:перепрыгнуть провал;DC:99;MODE:ADV"
    ) == {
        "type": "CHECK",
        "reason": "перепрыгнуть провал",
        "dc": 30,
        "mode": "ADVANTAGE",
    }
    assert dnd._parse_roll_command(
        "ROLL;TYPE:SAVE;REASON:не упасть;DC:1;MODE:DIS"
    ) == {
        "type": "SAVE",
        "reason": "не упасть",
        "dc": 5,
        "mode": "DISADVANTAGE",
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


def test_parse_turn_stores_story_roll_without_characteristic(monkeypatch):
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
        "reason": "не сорваться с карниза",
        "dc": 13,
        "mode": "ADVANTAGE",
    }
    assert session.last_roll_stat is None
    assert session.pending_actions == {}
    assert session.action_prompt_message_id is None
    assert "Спасбросок: не сорваться с карниза" in bot.messages[-1][1]
    assert "DC 13" in bot.messages[-1][1]
    assert "преимущество" in bot.messages[-1][1]


def test_handle_roll_reports_success_and_sends_story_context_to_master(monkeypatch):
    chat_id = -100702
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ROLL",
        pending_roll={
            "type": "SAVE",
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
        "🎯 5 и 18 → 18 против 12 — ✅ успех · преимущество"
    )
    assert "DC: 12; результат: успех" in prompts[0]
    assert "Броски d20: [5, 18]; итог: 18" in prompts[0]
    assert parsed == [(message.bot, chat_id, "продолжение [ACTION:INPUT]")]


def test_handle_roll_simplifies_normal_failure_summary(monkeypatch):
    chat_id = -100705
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ROLL",
        pending_roll={
            "type": "SAVE",
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
        raise RuntimeError("stop after output")

    async def fake_open_action_window(_bot, _chat_id):
        return None

    monkeypatch.setattr(dnd, "generate_session_response", fake_generate)
    monkeypatch.setattr(dnd, "open_action_window", fake_open_action_window)
    message = FakeMessage(chat_id=chat_id, user_name="Alina")

    try:
        asyncio.run(dnd.handle_roll(message))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert message.answers[0][0] == (
        "🎲 Alina: Спасбросок — успеть выбежать из рушащегося здания\n"
        "🎯 2 против 12 — ❌ провал"
    )
    assert "d20" not in message.answers[0][0]
    assert "DC" not in message.answers[0][0]
    assert "|" not in message.answers[0][0]


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
        "reason": "проверка по ситуации",
        "dc": None,
        "mode": "NORMAL",
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
        "reason": "проверка по ситуации",
        "dc": 15,
        "mode": "DISADVANTAGE",
    }
    assert "stat" not in session.pending_roll
