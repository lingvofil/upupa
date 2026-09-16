import asyncio
from types import SimpleNamespace

from AI import dnd_unknown_action_recovery as recovery


def test_action_classifier_only_flags_unsupported_commands():
    assert recovery._unsupported_action("Текст без тега") is None
    assert recovery._unsupported_action("[ACTION:ROLL;TYPE:SAVE;DC:13]") is None
    assert recovery._unsupported_action("[ACTION:POLL;QUESTION:Куда?]") is None
    assert recovery._unsupported_action("[ACTION:INPUT]") is None
    assert recovery._unsupported_action("[ACTION:END]") is None
    assert recovery._unsupported_action("[ACTION:SAVE;TARGETS:1;DC:13]") == "SAVE;TARGETS:1;DC:13"


def test_unknown_action_reopens_window_when_parser_leaves_session_resolving():
    session = SimpleNamespace(state="RESOLVING")
    calls = []

    async def base_parse(_bot, _chat_id, response):
        calls.append(("parse", response))

    async def open_action_window(_bot, chat_id):
        calls.append(("open", chat_id))
        session.state = "WAITING_ACTION"

    fake_dnd = SimpleNamespace(
        parse_and_execute_turn=base_parse,
        open_action_window=open_action_window,
        dnd_sessions={123: session},
    )
    recovery.install_dnd_unknown_action_recovery(fake_dnd)

    asyncio.run(
        fake_dnd.parse_and_execute_turn(
            None,
            123,
            "Газ режет лёгкие. [ACTION:SAVE;TARGETS:1;DC:13;REASON:ядовитый газ]",
        )
    )

    assert session.state == "WAITING_ACTION"
    assert calls[-1] == ("open", 123)


def test_unknown_action_does_not_double_recover_when_an_inner_layer_handled_it():
    session = SimpleNamespace(state="RESOLVING")
    calls = []

    async def handled_parse(_bot, _chat_id, _response):
        session.state = "WAITING_ROLL"

    async def open_action_window(_bot, chat_id):
        calls.append(("open", chat_id))

    fake_dnd = SimpleNamespace(
        parse_and_execute_turn=handled_parse,
        open_action_window=open_action_window,
        dnd_sessions={123: session},
    )
    recovery.install_dnd_unknown_action_recovery(fake_dnd)

    asyncio.run(fake_dnd.parse_and_execute_turn(None, 123, "[ACTION:ENEMY_ATTACK;TARGETS:1]"))

    assert session.state == "WAITING_ROLL"
    assert calls == []


def test_known_base_action_is_never_recovered_by_wrapper():
    session = SimpleNamespace(state="RESOLVING")
    calls = []

    async def base_parse(_bot, _chat_id, _response):
        return None

    async def open_action_window(_bot, chat_id):
        calls.append(("open", chat_id))

    fake_dnd = SimpleNamespace(
        parse_and_execute_turn=base_parse,
        open_action_window=open_action_window,
        dnd_sessions={123: session},
    )
    recovery.install_dnd_unknown_action_recovery(fake_dnd)

    asyncio.run(fake_dnd.parse_and_execute_turn(None, 123, "[ACTION:ROLL;TYPE:SAVE;DC:13]"))

    assert calls == []
