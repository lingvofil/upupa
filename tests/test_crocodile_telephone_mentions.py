import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_telephone_turn_mentions_current_player_and_escapes_name(monkeypatch):
    from games import crocodile_telephone_mentions as mentions

    send_message = AsyncMock()
    monkeypatch.setattr(
        mentions.crocodile_modes,
        "bot",
        SimpleNamespace(send_message=send_message),
    )
    monkeypatch.setattr(mentions.crocodile_modes, "_blank", lambda: b"blank")
    monkeypatch.setattr(
        mentions.crocodile_modes,
        "_canvas_button",
        lambda chat_id, suffix, text: (chat_id, suffix, text),
    )

    game = {
        "step": 1,
        "players": [(101, "Первый"), (202, "М&M <artist>")],
        "chain": [{"kind": "text", "value": "крокодил"}],
    }

    asyncio.run(mentions.send_telephone_step_with_mention("-42", game))

    send_message.assert_awaited_once()
    args, kwargs = send_message.await_args
    assert args[0] == -42
    assert '<a href="tg://user?id=202">М&amp;M &lt;artist&gt;</a>' in args[1]
    assert ": рисуй. Остальные не подглядывают." in args[1]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"] == ("-42", "t1", "✍️ Открыть свой ход")

    session = mentions.crocodile_modes.canvas_sessions["-42:t1"]
    try:
        assert session["drawer_id"] == 202
        assert session["drawer_name"] == "М&M <artist>"
        assert session["word"] == "крокодил"
        assert session["ui_mode"] == "draw"
    finally:
        mentions.crocodile_modes.canvas_sessions.pop("-42:t1", None)


def test_telephone_first_turn_mentions_player_too(monkeypatch):
    from games import crocodile_telephone_mentions as mentions

    send_message = AsyncMock()
    monkeypatch.setattr(
        mentions.crocodile_modes,
        "bot",
        SimpleNamespace(send_message=send_message),
    )
    monkeypatch.setattr(mentions.crocodile_modes, "_blank", lambda: b"blank")
    monkeypatch.setattr(
        mentions.crocodile_modes,
        "_canvas_button",
        lambda *_args: None,
    )

    game = {
        "step": 0,
        "players": [(303, "Загадыватель")],
        "chain": [],
    }

    try:
        asyncio.run(mentions.send_telephone_step_with_mention("-43", game))
        text = send_message.await_args.args[1]
        assert '<a href="tg://user?id=303">Загадыватель</a>' in text
        assert ": загадывай. Остальные не подглядывают." in text
    finally:
        mentions.crocodile_modes.canvas_sessions.pop("-43:t0", None)

ROOT = Path(__file__).resolve().parents[1]


def test_telephone_step_configurator_drives_stable_entrypoint():
    from games import crocodile_modes as modes

    original = modes.get_send_telephone_step_handler()
    stable_entrypoint = modes._send_telephone_step
    configured = AsyncMock(return_value="configured-result")
    game = {"step": 0, "players": []}

    try:
        modes.configure_send_telephone_step_handler(configured)
        assert modes.get_send_telephone_step_handler() is configured
        assert modes.get_default_send_telephone_step_handler() is not configured
        assert modes._send_telephone_step is stable_entrypoint
        result = asyncio.run(modes._send_telephone_step("-44", game))
    finally:
        modes.configure_send_telephone_step_handler(original)

    assert result == "configured-result"
    configured.assert_awaited_once_with("-44", game)


def test_telephone_step_sender_is_composed_only_in_runtime():
    mentions_source = (
        ROOT / "games" / "crocodile_telephone_mentions.py"
    ).read_text(encoding="utf-8")
    party_source = (
        ROOT / "games" / "crocodile_party_controls.py"
    ).read_text(encoding="utf-8")
    modes_source = (
        ROOT / "games" / "crocodile_modes.py"
    ).read_text(encoding="utf-8")
    runtime_source = (
        ROOT / "games" / "crocodile_runtime.py"
    ).read_text(encoding="utf-8")

    assert "_configured = False" not in mentions_source
    assert "_original_send_telephone_step" not in mentions_source
    assert "def configure_crocodile_telephone_mentions(" not in mentions_source
    assert "crocodile_party_controls" not in mentions_source

    assert "_original_send_telephone_step" not in party_source
    assert "crocodile_modes._send_telephone_step =" not in party_source
    assert (
        "send_telephone_step_with_controls(chat_id: str, game: dict, next_handler)"
        in party_source
    )

    assert "def get_default_send_telephone_step_handler(" in modes_source
    assert "def get_send_telephone_step_handler(" in modes_source
    assert "def configure_send_telephone_step_handler(" in modes_source

    composition = runtime_source.index(
        "telephone_step_handler = _compose_telephone_step_sender("
    )
    mention = runtime_source.index(
        "send_telephone_step_with_mention,",
        composition,
    )
    controls = runtime_source.index(
        "party_controls.send_telephone_step_with_controls,",
        mention,
    )
    wiring = runtime_source.index(
        "crocodile_modes.configure_send_telephone_step_handler(",
        controls,
    )

    assert "configure_crocodile_telephone_mentions(" not in runtime_source
    assert runtime_source.count(
        "crocodile_modes.configure_send_telephone_step_handler("
    ) == 1
    assert composition < mention < controls < wiring

