import asyncio
from types import SimpleNamespace

from AI import dnd_poll_agency as agency


class FakeBot:
    def __init__(self):
        self.stopped = []
        self.messages = []

    async def stop_poll(self, chat_id, message_id):
        self.stopped.append((chat_id, message_id))
        return SimpleNamespace()

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=900 + len(self.messages))


def _session(chat_id=-10071):
    return SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        state="WAITING_POLL",
        current_poll_id="poll-1",
        pending_poll={
            "poll_id": "poll-1",
            "message_id": 55,
            "target_user_ids": [],
            "votes": {},
            "scene_text": "Староста требует решить, что делать дальше.",
        },
        last_resolved_poll=None,
        action_prompt_message_id=None,
        pending_actions={},
        action_deadline=None,
        action_target_user_ids=[],
    )


def _fake_dnd(session, counts):
    parsed = []
    finalized = []
    opened = []
    persisted = []

    async def parse(_bot, _chat_id, response):
        parsed.append(response)

    async def finalize(_bot, _chat_id, _message_id, options):
        finalized.append(list(options))
        return "original"

    async def open_action(_bot, _chat_id, target_user_ids=None):
        opened.append(list(target_user_ids or []))
        session.state = "WAITING_ACTION"
        session.action_prompt_message_id = 777
        return SimpleNamespace(message_id=777)

    dnd = SimpleNamespace(
        DND_SYSTEM_PROMPT="base",
        parse_and_execute_turn=parse,
        finalize_poll=finalize,
        dnd_sessions={session.chat_id: session},
        poll_map={"poll-1": session.chat_id},
        _finalizing_polls=set(),
        _is_participant_mode=lambda current: current is session,
        _eligible_poll_vote_counts=lambda _session, _options: list(counts),
        persist_dnd_sessions=lambda: persisted.append(True),
        open_action_window=open_action,
    )
    return dnd, parsed, finalized, opened, persisted


def test_poll_rules_require_text_and_options_to_match():
    rules = agency.POLL_AGENCY_RULES.casefold()

    assert "обязан присутствовать" in rules
    assert "не прячь его из кнопок" in rules
    assert "используй action:input" in rules


def test_group_poll_gets_three_authored_options_plus_free_choice():
    source = (
        "Можно договориться, уйти, спрятаться или полезть в драку. "
        "[ACTION:POLL;OPTIONS:Договориться;Уйти;Спрятаться;Драться]"
    )

    result = agency.ensure_group_poll_free_choice(source)

    assert "Договориться" in result
    assert "Уйти" in result
    assert "Спрятаться" in result
    assert "Драться" not in result
    assert result.endswith(
        f"[ACTION:POLL;OPTIONS:Договориться;Уйти;Спрятаться;{agency.CUSTOM_POLL_OPTION}]"
    )


def test_personal_poll_keeps_exact_model_options():
    source = (
        "Алисе выбирать. "
        "[ACTION:POLL;TARGETS:11;OPTIONS:Открыть письмо;Сжечь письмо]"
    )

    assert agency.ensure_group_poll_free_choice(source) == source


def test_parse_wrapper_adds_free_choice_before_core_parser():
    session = _session()
    dnd, parsed, _finalized, _opened, _persisted = _fake_dnd(
        session,
        counts=[0, 0, 0],
    )
    agency.install_dnd_poll_agency(dnd)

    asyncio.run(
        dnd.parse_and_execute_turn(
            object(),
            session.chat_id,
            "Что делать? [ACTION:POLL;OPTIONS:Спорить;Уйти]",
        )
    )

    assert len(parsed) == 1
    assert agency.CUSTOM_POLL_OPTION in parsed[0]


def test_custom_choice_wins_tie_and_opens_free_action():
    session = _session()
    options = ["Спорить", "Уйти", agency.CUSTOM_POLL_OPTION]
    session.pending_poll["options"] = list(options)
    session.pending_poll["votes"] = {"1": 0, "2": 2}
    dnd, _parsed, finalized, opened, persisted = _fake_dnd(
        session,
        counts=[1, 0, 1],
    )
    agency.install_dnd_poll_agency(dnd)
    bot = FakeBot()

    result = asyncio.run(
        dnd.finalize_poll(bot, session.chat_id, 55, options)
    )

    assert result is True
    assert finalized == []
    assert bot.stopped == [(session.chat_id, 55)]
    assert any("Свой вариант" in text for _chat, text, _kwargs in bot.messages)
    assert opened == [[]]
    assert session.state == "WAITING_ACTION"
    assert session.current_poll_id is None
    assert session.pending_poll is None
    assert session.action_prompt_message_id == 777
    assert session.last_resolved_poll["outcome"] == f"Выбор сделан: {agency.CUSTOM_POLL_OPTION}"
    assert persisted


def test_nonwinning_custom_vote_is_ignored_by_normal_resolution():
    session = _session()
    options = ["Спорить", "Уйти", agency.CUSTOM_POLL_OPTION]
    session.pending_poll["options"] = list(options)
    dnd, _parsed, finalized, opened, _persisted = _fake_dnd(
        session,
        counts=[2, 1, 1],
    )
    agency.install_dnd_poll_agency(dnd)

    result = asyncio.run(
        dnd.finalize_poll(FakeBot(), session.chat_id, 55, options)
    )

    assert result == "original"
    assert finalized == [["Спорить", "Уйти"]]
    assert opened == []
