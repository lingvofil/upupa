import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI import dnd_state_commands as commands


class FakeMessage:
    def __init__(self, text, *, chat_id=-100777, user_id=7, first_name="Семён"):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(id=user_id, first_name=first_name)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=900 + len(self.answers))


def _session(*, chat_id=-100777, host_id=7):
    return SimpleNamespace(
        chat_id=chat_id,
        mode="participants",
        state="LOBBY",
        starter_user_id=host_id,
        starter_name="Семён",
        participants={"7": {"user_id": 7, "name": "Семён"}},
        lobby_message_id=123,
        plot_options=[],
    )


def _campaign(*, missing=None, plots=None):
    plots = plots or ["Сюжет А", "Сюжет Б", "Сюжет В", "Сюжет Г", "Сюжет Д"]

    async def plot_choices(_dnd, _session):
        return list(plots)

    return SimpleNamespace(
        _ensure=lambda _session: None,
        _lobby_text=lambda session: "Лобби: " + ", ".join(
            item["name"] for item in session.participants.values()
        ),
        _lobby_keyboard=lambda _session: "lobby-keyboard",
        _missing_profiles=lambda _session: list(missing or []),
        _plot_choices=plot_choices,
        _plot_keyboard=lambda options, include_custom: (tuple(options), include_custom),
    )


def _run(message):
    async def downstream(_event, _data):
        raise AssertionError("DnD short command must not fall through")

    return asyncio.run(commands.DndStateCommandMiddleware()(downstream, message, {}))


def test_short_command_aliases_are_exact_and_ignore_trailing_punctuation():
    assert commands.command_kind("днд") == "lobby_menu"
    assert commands.command_kind("ДНД!!!") == "lobby_menu"
    assert commands.command_kind("днд старт") == "lobby_start"
    assert commands.command_kind("  ДНД   СТАРТ?! ") == "lobby_start"
    assert commands.command_kind("днд начинай") is None
    assert commands.is_state_command("днд") is True
    assert commands.is_state_command("днд старт") is True


def test_dnd_command_reposts_current_lobby_and_tracks_new_message(monkeypatch):
    session = _session()
    persisted = []
    monkeypatch.setattr(dnd, "dnd_sessions", {session.chat_id: session})
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))
    monkeypatch.setattr(commands, "_campaign_module", lambda _dnd: _campaign())

    message = FakeMessage("днд", chat_id=session.chat_id)
    result = _run(message)

    assert result is None
    assert message.answers == [("Лобби: Семён", {"reply_markup": "lobby-keyboard"})]
    assert session.lobby_message_id == 901
    assert persisted == [True]


def test_dnd_start_uses_same_plot_selection_flow(monkeypatch):
    session = _session()
    persisted = []
    plot_options = ["Один", "Два", "Три", "Четыре", "Пять"]
    monkeypatch.setattr(dnd, "dnd_sessions", {session.chat_id: session})
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))
    monkeypatch.setattr(commands, "_campaign_module", lambda _dnd: _campaign(plots=plot_options))

    message = FakeMessage("днд старт", chat_id=session.chat_id, user_id=7)
    result = _run(message)

    assert result is None
    assert session.state == "WAITING_PLOT"
    assert session.plot_options == plot_options
    assert persisted == [True]
    text, kwargs = message.answers[0]
    assert text.startswith("🎬 Выбери сюжет:\n\n1. Один\n2. Два")
    assert kwargs["reply_markup"] == (tuple(plot_options), False)


def test_dnd_start_keeps_host_and_profile_guards(monkeypatch):
    session = _session(host_id=42)
    monkeypatch.setattr(dnd, "dnd_sessions", {session.chat_id: session})
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)
    monkeypatch.setattr(commands, "_campaign_module", lambda _dnd: _campaign())

    outsider = FakeMessage("днд старт", chat_id=session.chat_id, user_id=7)
    _run(outsider)
    assert outsider.answers == [("Запустить игру может только ведущий.", {})]
    assert session.state == "LOBBY"

    session.starter_user_id = 7
    monkeypatch.setattr(commands, "_campaign_module", lambda _dnd: _campaign(missing=["Семён"]))
    host = FakeMessage("днд старт", chat_id=session.chat_id, user_id=7)
    _run(host)
    assert host.answers == [("Не готовы: Семён", {})]
    assert session.state == "LOBBY"


def test_short_commands_explain_when_lobby_is_not_open(monkeypatch):
    monkeypatch.setattr(dnd, "dnd_sessions", {})
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: None)

    menu = FakeMessage("днд")
    _run(menu)
    assert "открытого лобби ДНД нет" in menu.answers[0][0]

    start = FakeMessage("днд старт")
    _run(start)
    assert start.answers == [("Сейчас открытого лобби ДНД нет.", {})]
