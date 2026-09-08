import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd


class RecordingSupervisor:
    def __init__(self):
        self.names = []

    def start(self, coro, *, name):
        self.names.append(name)
        coro.close()
        return name


class FakeMessage:
    def __init__(
        self,
        *,
        chat_id,
        user_id,
        user_name,
        text,
        reply_to_message_id=None,
        bot=None,
    ):
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, first_name=user_name)
        self.text = text
        self.caption = None
        self.bot = bot or object()
        self.reply_to_message = (
            SimpleNamespace(message_id=reply_to_message_id)
            if reply_to_message_id is not None
            else None
        )
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.messages))


def test_dnd_poll_timeout_is_three_minutes():
    assert dnd.DND_POLL_TIMEOUT_SECONDS == 180
    assert dnd.DND_ACTION_WINDOW_SECONDS == 180


def test_scene_director_does_not_repeat_last_two(monkeypatch):
    session = SimpleNamespace(recent_scene_types=[])
    monkeypatch.setattr(dnd.random, "choice", lambda items: items[0])

    picked = [dnd.choose_next_scene_type(session) for _ in range(6)]

    for index, scene_type in enumerate(picked):
        assert scene_type not in picked[max(0, index - 2):index]
    assert len(session.recent_scene_types) <= dnd.DND_RECENT_SCENE_LIMIT


def test_roll_prompt_uses_human_difficulty_label(monkeypatch):
    chat_id = -100499
    session = SimpleNamespace(
        chat_id=chat_id,
        mode="abstract",
        state="RESOLVING",
        pending_roll=None,
        last_roll_stat=None,
        action_prompt_message_id=None,
        pending_actions={},
        action_deadline=None,
        action_target_user_ids=[],
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    bot = FakeBot()

    try:
        asyncio.run(
            dnd.parse_and_execute_turn(
                bot,
                chat_id,
                "Кабан несётся прямо на тебя. "
                "[ACTION:ROLL;TYPE:SAVE;REASON:успеть увернуться от тарана кабана;DC:12;MODE:NORMAL]",
            )
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    prompt = bot.messages[-1][1]
    assert "сложность 12" in prompt
    assert "DC 12" not in prompt


def test_group_action_router_only_accepts_replies_to_current_prompt():
    chat_id = -100500
    session = SimpleNamespace(
        state="WAITING_ACTION",
        action_prompt_message_id=77,
    )
    dnd.dnd_sessions[chat_id] = session

    try:
        plain_chat = FakeMessage(
            chat_id=chat_id,
            user_id=1,
            user_name="Алиса",
            text="обычная болтовня",
        )
        wrong_reply = FakeMessage(
            chat_id=chat_id,
            user_id=1,
            user_name="Алиса",
            text="не туда",
            reply_to_message_id=76,
        )
        correct_reply = FakeMessage(
            chat_id=chat_id,
            user_id=1,
            user_name="Алиса",
            text="ломаю дверь",
            reply_to_message_id=77,
        )

        assert dnd._is_group_action_reply(plain_chat) is False
        assert dnd._is_group_action_reply(wrong_reply) is False
        assert dnd._is_group_action_reply(correct_reply) is True
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_participant_mode_restricts_targeted_turn_to_named_player():
    chat_id = -1005001
    session = SimpleNamespace(
        mode="participants",
        state="WAITING_ACTION",
        action_prompt_message_id=177,
        action_target_user_ids=[1],
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
    )
    dnd.dnd_sessions[chat_id] = session

    try:
        alice = FakeMessage(
            chat_id=chat_id,
            user_id=1,
            user_name="Алиса",
            text="прыгаю в окно",
            reply_to_message_id=177,
        )
        boris = FakeMessage(
            chat_id=chat_id,
            user_id=2,
            user_name="Боря",
            text="тоже прыгаю",
            reply_to_message_id=177,
        )
        outsider = FakeMessage(
            chat_id=chat_id,
            user_id=3,
            user_name="Вася",
            text="а я дверь открою",
            reply_to_message_id=177,
        )

        assert dnd._is_group_action_reply(alice) is True
        assert dnd._is_group_action_reply(boris) is False
        assert dnd._is_group_action_reply(outsider) is False
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_group_turn_collects_players_and_starts_one_timer(monkeypatch):
    chat_id = -100501
    session = SimpleNamespace(
        state="WAITING_ACTION",
        action_prompt_message_id=88,
        pending_actions={},
        action_deadline=None,
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd.time, "time", lambda: 1000.0)
    supervisor = RecordingSupervisor()
    monkeypatch.setattr(dnd, "_task_supervisor", supervisor)

    first = FakeMessage(
        chat_id=chat_id,
        user_id=1,
        user_name="Алиса",
        text="ломаю дверь",
        reply_to_message_id=88,
    )
    second = FakeMessage(
        chat_id=chat_id,
        user_id=2,
        user_name="Боря",
        text="прячусь за Алисой",
        reply_to_message_id=88,
    )

    try:
        asyncio.run(dnd.handle_free_action(first))
        asyncio.run(dnd.handle_free_action(second))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert session.pending_actions == {
        "1": {"user_id": 1, "name": "Алиса", "action": "ломаю дверь"},
        "2": {"user_id": 2, "name": "Боря", "action": "прячусь за Алисой"},
    }
    assert session.action_deadline == 1000.0 + dnd.DND_ACTION_WINDOW_SECONDS
    assert supervisor.names == [f"dnd-actions:{chat_id}:88"]
    assert len(first.answers) == 1
    assert second.answers == []


def test_player_can_replace_own_action_without_new_timer(monkeypatch):
    chat_id = -100502
    session = SimpleNamespace(
        state="WAITING_ACTION",
        action_prompt_message_id=99,
        pending_actions={},
        action_deadline=None,
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd.time, "time", lambda: 1000.0)
    supervisor = RecordingSupervisor()
    monkeypatch.setattr(dnd, "_task_supervisor", supervisor)

    try:
        asyncio.run(
            dnd.handle_free_action(
                FakeMessage(
                    chat_id=chat_id,
                    user_id=7,
                    user_name="Вася",
                    text="бью гоблина",
                    reply_to_message_id=99,
                )
            )
        )
        asyncio.run(
            dnd.handle_free_action(
                FakeMessage(
                    chat_id=chat_id,
                    user_id=7,
                    user_name="Вася",
                    text="нет, убегаю от гоблина",
                    reply_to_message_id=99,
                )
            )
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert session.pending_actions["7"]["action"] == "нет, убегаю от гоблина"
    assert supervisor.names == [f"dnd-actions:{chat_id}:99"]


def test_finalize_group_actions_sends_all_actions_to_master(monkeypatch):
    chat_id = -100503
    session = SimpleNamespace(
        chat_id=chat_id,
        state="WAITING_ACTION",
        action_prompt_message_id=111,
        pending_actions={
            "1": {"user_id": 1, "name": "Алиса", "action": "ломаю дверь"},
            "2": {"user_id": 2, "name": "Боря", "action": "ищу ловушку"},
        },
        action_deadline=1234.0,
        recent_scene_types=[],
    )
    dnd.dnd_sessions[chat_id] = session
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(dnd, "with_scene_direction", lambda _session, prompt: prompt)

    prompts = []
    parsed = []

    async def fake_generate(_session, prompt):
        prompts.append(prompt)
        return "результат [ACTION:INPUT]"

    async def fake_parse(bot, resolved_chat_id, text):
        parsed.append((bot, resolved_chat_id, text))

    monkeypatch.setattr(dnd, "generate_session_response", fake_generate)
    monkeypatch.setattr(dnd, "parse_and_execute_turn", fake_parse)
    bot = FakeBot()

    try:
        asyncio.run(dnd.finalize_group_actions(bot, chat_id, 111))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert len(prompts) == 1
    assert "Алиса: ломаю дверь" in prompts[0]
    assert "Боря: ищу ловушку" in prompts[0]
    assert "одновременно" in prompts[0]
    assert parsed == [(bot, chat_id, "результат [ACTION:INPUT]")]
    assert session.state == "RESOLVING"
    assert session.pending_actions == {}
    assert session.action_prompt_message_id is None


def test_participant_poll_counts_only_registered_target_votes():
    session = SimpleNamespace(
        mode="participants",
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        pending_poll={
            "target_user_ids": [1],
            "votes": {"1": 0},
        },
    )

    assert dnd._poll_user_is_eligible(session, 1) is True
    assert dnd._poll_user_is_eligible(session, 2) is False
    assert dnd._poll_user_is_eligible(session, 3) is False
    assert dnd._eligible_poll_vote_counts(session, ["A", "B"]) == [1, 0]


def test_only_host_can_finish_action_window_early(monkeypatch):
    chat_id = -100504
    session = SimpleNamespace(
        starter_user_id=1,
        state="WAITING_ACTION",
        action_prompt_message_id=222,
        pending_actions={"2": {"user_id": 2, "name": "Боря", "action": "иду"}},
    )
    dnd.dnd_sessions[chat_id] = session
    calls = []

    async def fake_finalize(bot, resolved_chat_id, prompt_id):
        calls.append((bot, resolved_chat_id, prompt_id))

    monkeypatch.setattr(dnd, "finalize_group_actions", fake_finalize)
    bot = object()
    non_host = FakeMessage(
        chat_id=chat_id,
        user_id=2,
        user_name="Боря",
        text="дальше",
        bot=bot,
    )
    host = FakeMessage(
        chat_id=chat_id,
        user_id=1,
        user_name="Алиса",
        text="дальше",
        bot=bot,
    )

    try:
        asyncio.run(dnd.handle_dnd_next(non_host))
        asyncio.run(dnd.handle_dnd_next(host))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == [(bot, chat_id, 222)]
    assert non_host.answers[0][0] == "«Дальше» может сказать только ведущий."
