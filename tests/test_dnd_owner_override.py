import asyncio
from pathlib import Path
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd, dnd_state_commands
from core.settings import ADMIN_ID


def test_admin_and_real_host_are_authorized_without_mutating_session():
    session = SimpleNamespace(starter_user_id=42)

    assert dnd._user_is_host(session, 42) is True
    assert dnd._user_is_host(session, ADMIN_ID) is True
    assert dnd._user_is_host(session, 99) is False
    assert session.starter_user_id == 42


def test_admin_check_cannot_temporarily_lock_out_real_host():
    session = SimpleNamespace(starter_user_id=42)

    async def scenario():
        admin_started = asyncio.Event()
        release_admin = asyncio.Event()

        async def admin_command():
            assert dnd._user_is_host(session, ADMIN_ID) is True
            admin_started.set()
            await release_admin.wait()
            assert session.starter_user_id == 42

        async def real_host_command():
            await admin_started.wait()
            authorized = dnd._user_is_host(session, 42)
            release_admin.set()
            return authorized

        admin_task = asyncio.create_task(admin_command())
        host_authorized = await real_host_command()
        await admin_task
        return host_authorized

    assert asyncio.run(scenario()) is True
    assert session.starter_user_id == 42


def test_admin_callback_uses_same_host_check():
    session = SimpleNamespace(starter_user_id=42)
    admin_callback = SimpleNamespace(from_user=SimpleNamespace(id=ADMIN_ID))
    host_callback = SimpleNamespace(from_user=SimpleNamespace(id=42))

    assert dnd._callback_is_host(admin_callback, session) is True
    assert dnd._callback_is_host(host_callback, session) is True
    assert session.starter_user_id == 42


def test_admin_can_use_next_without_changing_real_host(monkeypatch):
    chat_id = -1009004
    session = SimpleNamespace(
        starter_user_id=42,
        state="WAITING_ACTION",
        pending_actions={"7": {"action": "иду"}},
        action_prompt_message_id=123,
    )
    dnd.dnd_sessions[chat_id] = session
    calls = []

    async def fake_finalize(bot, resolved_chat_id, prompt_message_id):
        calls.append((bot, resolved_chat_id, prompt_message_id))

    monkeypatch.setattr(dnd, "finalize_group_actions", fake_finalize)
    bot = object()
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=ADMIN_ID),
        bot=bot,
        answer=lambda *_args, **_kwargs: None,
    )

    try:
        asyncio.run(dnd.handle_dnd_next(message))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == [(bot, chat_id, 123)]
    assert session.starter_user_id == 42


def test_admin_can_start_lobby_from_text_command(monkeypatch):
    session = SimpleNamespace(
        starter_user_id=42,
        state="LOBBY",
        mode="participants",
        participants={"7": {"user_id": 7, "name": "Егрок"}},
        plot_options=[],
    )
    answers = []

    async def answer(text, **kwargs):
        answers.append((text, kwargs))
        return SimpleNamespace(message_id=1)

    event = SimpleNamespace(
        chat=SimpleNamespace(id=-1009005),
        from_user=SimpleNamespace(id=ADMIN_ID),
        answer=answer,
    )
    campaign = SimpleNamespace(
        _missing_profiles=lambda _session: [],
        _plot_choices=lambda _dnd, _session: _async_value(["Сюжет"]),
        _plot_keyboard=lambda _options, _flag: "keyboard",
    )

    monkeypatch.setattr(
        dnd_state_commands,
        "_open_participant_lobby",
        lambda _dnd, _chat_id: session,
    )
    monkeypatch.setattr(dnd_state_commands, "_campaign_module", lambda _dnd: campaign)
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)

    asyncio.run(dnd_state_commands._start_lobby_from_message(event, dnd))

    assert session.state == "WAITING_PLOT"
    assert session.starter_user_id == 42
    assert session.plot_options == ["Сюжет"]
    assert answers[-1][1]["reply_markup"] == "keyboard"


async def _async_value(value):
    return value


def test_bootstrap_no_longer_rewrites_dnd_host_identity():
    import app.bootstrap as bootstrap

    source = Path(bootstrap.__file__).read_text(encoding="utf-8")

    assert "DndOwnerHostOverrideMiddleware" not in source
    assert "session.starter_user_id = int(ADMIN_ID)" not in source
    assert "_configure_dnd_owner_host_override" not in source
