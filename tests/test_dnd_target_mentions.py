import asyncio
import re
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI.dnd_target_mentions import configure_dnd_target_mentions


class FakeBot:
    def __init__(self, *, fail=False):
        self.messages = []
        self.fail = fail

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail:
            raise RuntimeError("telegram unavailable")
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=100 + len(self.messages))


def _parse_targets(command):
    match = re.search(r"(?:^|;)TARGETS:([0-9,\s]+)(?:;|$)", command, flags=re.IGNORECASE)
    if not match:
        return []
    result = []
    for token in match.group(1).split(","):
        token = token.strip()
        if token.isdigit() and int(token) not in result:
            result.append(int(token))
    return result


def _fake_dnd():
    chat_id = -100777
    session = SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        participants={
            "1": {"user_id": 1, "name": "Алиса <Быстрая>"},
            "2": {"user_id": 2, "name": "Боря"},
            "3": {"user_id": 3, "name": "Вася"},
        },
    )
    open_calls = []
    parse_calls = []

    async def original_open(bot, resolved_chat_id, target_user_ids=None):
        open_calls.append((bot, resolved_chat_id, list(target_user_ids or [])))
        return "opened"

    async def original_parse(bot, resolved_chat_id, text_response):
        parse_calls.append((bot, resolved_chat_id, text_response))
        return "parsed"

    def participant_name(target_session, user_id):
        return target_session.participants[str(int(user_id))]["name"]

    def resolve_targets(target_session, raw_targets):
        if getattr(target_session, "mode", None) != "participants":
            return []
        participant_ids = {int(key) for key in target_session.participants}
        return [int(value) for value in raw_targets if int(value) in participant_ids]

    fake = SimpleNamespace(
        dnd_sessions={chat_id: session},
        open_action_window=original_open,
        parse_and_execute_turn=original_parse,
        _participant_name=participant_name,
        _resolve_targets=resolve_targets,
        _parse_targets=_parse_targets,
    )
    return fake, session, open_calls, parse_calls


def test_targeted_input_tags_player_and_keeps_action_flow():
    fake, session, open_calls, _parse_calls = _fake_dnd()
    configure_dnd_target_mentions(fake)
    bot = FakeBot()

    result = asyncio.run(fake.open_action_window(bot, session.chat_id, target_user_ids=[1]))

    assert result == "opened"
    assert len(bot.messages) == 1
    chat_id, text, kwargs = bot.messages[0]
    assert chat_id == session.chat_id
    assert 'tg://user?id=1' in text
    assert "Алиса &lt;Быстрая&gt;" in text
    assert "твой ход" in text
    assert kwargs == {"parse_mode": "HTML"}
    assert open_calls == [(bot, session.chat_id, [1])]


def test_targeted_roll_and_poll_tag_up_to_two_targets():
    fake, session, _open_calls, parse_calls = _fake_dnd()
    configure_dnd_target_mentions(fake)
    bot = FakeBot()

    asyncio.run(
        fake.parse_and_execute_turn(
            bot,
            session.chat_id,
            "Проверка [ACTION:ROLL;TYPE:CHECK;TARGETS:1;DC:10;MODE:NORMAL]",
        )
    )
    asyncio.run(
        fake.parse_and_execute_turn(
            bot,
            session.chat_id,
            "Выбор [ACTION:POLL;TARGETS:1,2;OPTIONS:A;B]",
        )
    )

    assert len(bot.messages) == 2
    assert 'tg://user?id=1' in bot.messages[0][1]
    assert "твой бросок" in bot.messages[0][1]
    assert 'tg://user?id=1' in bot.messages[1][1]
    assert 'tg://user?id=2' in bot.messages[1][1]
    assert "ваш выбор" in bot.messages[1][1]
    assert len(parse_calls) == 2


def test_three_or_more_targets_are_not_tagged_but_action_flow_continues():
    fake, session, open_calls, parse_calls = _fake_dnd()
    configure_dnd_target_mentions(fake)
    bot = FakeBot()

    result = asyncio.run(
        fake.open_action_window(bot, session.chat_id, target_user_ids=[1, 2, 3])
    )
    asyncio.run(
        fake.parse_and_execute_turn(
            bot,
            session.chat_id,
            "Проверка [ACTION:ROLL;TYPE:CHECK;TARGETS:1,2,3;DC:10;MODE:NORMAL]",
        )
    )
    asyncio.run(
        fake.parse_and_execute_turn(
            bot,
            session.chat_id,
            "Выбор [ACTION:POLL;TARGETS:1,2,3;OPTIONS:A;B]",
        )
    )

    assert result == "opened"
    assert bot.messages == []
    assert open_calls == [(bot, session.chat_id, [1, 2, 3])]
    assert len(parse_calls) == 2


def test_general_actions_are_not_tagged():
    fake, session, _open_calls, parse_calls = _fake_dnd()
    configure_dnd_target_mentions(fake)
    bot = FakeBot()

    asyncio.run(fake.open_action_window(bot, session.chat_id))
    asyncio.run(
        fake.parse_and_execute_turn(
            bot,
            session.chat_id,
            "Общий бросок [ACTION:ROLL;TYPE:CHECK;DC:10;MODE:NORMAL]",
        )
    )
    asyncio.run(
        fake.parse_and_execute_turn(
            bot,
            session.chat_id,
            "Общий выбор [ACTION:POLL;OPTIONS:A;B]",
        )
    )

    assert bot.messages == []
    assert len(parse_calls) == 2


def test_mention_failure_never_blocks_targeted_action():
    fake, session, open_calls, _parse_calls = _fake_dnd()
    configure_dnd_target_mentions(fake)
    bot = FakeBot(fail=True)

    result = asyncio.run(fake.open_action_window(bot, session.chat_id, target_user_ids=[1]))

    assert result == "opened"
    assert open_calls == [(bot, session.chat_id, [1])]


def test_configuration_is_idempotent_and_does_not_double_ping():
    fake, session, _open_calls, _parse_calls = _fake_dnd()
    configure_dnd_target_mentions(fake)
    configure_dnd_target_mentions(fake)
    bot = FakeBot()

    asyncio.run(fake.open_action_window(bot, session.chat_id, target_user_ids=[2]))

    assert len(bot.messages) == 1
    assert 'tg://user?id=2' in bot.messages[0][1]
