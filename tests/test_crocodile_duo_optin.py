import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _base_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Открыть холст", url="https://example.com")],
            [InlineKeyboardButton(text="👥 Рисовать вдвоём", callback_data=f"cr_duo_{chat_id}")],
        ]
    )


def _callback(data: str, user_id: int, name: str = "Игрок"):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-42),
        answer=AsyncMock(),
        edit_reply_markup=AsyncMock(),
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, full_name=name),
        message=message,
        answer=AsyncMock(),
    )


def test_duo_keyboard_shows_artist_invite_before_join(monkeypatch):
    from games import crocodile
    from games import crocodile_duo_optin as duo

    chat_id = -42
    crocodile.game_sessions[str(chat_id)] = {
        "drawer_id": 1,
        "drawer_name": "Первый",
    }
    monkeypatch.setattr(duo, "_original_get_game_keyboard", _base_keyboard)

    try:
        keyboard = duo.get_game_keyboard_with_duo_opt_in(chat_id)
        duo_buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
            if (button.callback_data or "").startswith("cr_duo_")
        ]
        assert len(duo_buttons) == 1
        assert duo_buttons[0].callback_data == "cr_duo_invite_-42"
        assert "решает художник" in duo_buttons[0].text

        crocodile.game_sessions[str(chat_id)]["duo_invite_open"] = True
        keyboard = duo.get_game_keyboard_with_duo_opt_in(chat_id)
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        assert "cr_duo_join_-42" in callbacks
        assert "cr_duo_invite_-42" not in callbacks
    finally:
        crocodile.game_sessions.pop(str(chat_id), None)


def test_non_artist_cannot_open_duo_invitation(monkeypatch):
    from games import crocodile
    from games import crocodile_duo_optin as duo

    crocodile.game_sessions["-42"] = {
        "drawer_id": 1,
        "drawer_name": "Первый",
    }
    persist = MagicMock()
    monkeypatch.setattr(duo, "_persist_regular_state", persist)
    callback = _callback("cr_duo_invite_-42", 2, "Второй")

    try:
        asyncio.run(duo.handle_duo_opt_in_callback(callback))
        assert "duo_invite_open" not in crocodile.game_sessions["-42"]
        persist.assert_not_called()
        callback.answer.assert_awaited_once_with(
            "Сначала текущий художник должен сам позвать напарника.",
            show_alert=True,
        )
    finally:
        crocodile.game_sessions.pop("-42", None)


def test_primary_artist_must_invite_before_second_can_join(monkeypatch):
    from games import crocodile
    from games import crocodile_duo_optin as duo

    session = {
        "drawer_id": 1,
        "drawer_name": "Первый",
    }
    crocodile.game_sessions["-42"] = session
    persist = MagicMock()
    monkeypatch.setattr(duo, "_persist_regular_state", persist)
    monkeypatch.setattr(duo, "_original_get_game_keyboard", _base_keyboard)

    early_join = _callback("cr_duo_join_-42", 2, "Второй")
    primary = _callback("cr_duo_invite_-42", 1, "Первый")
    second = _callback("cr_duo_join_-42", 2, "Второй")

    try:
        asyncio.run(duo.handle_duo_opt_in_callback(early_join))
        assert "drawer_ids" not in session
        early_join.answer.assert_awaited_once_with(
            "Первый художник ещё не открывал совместное рисование.",
            show_alert=True,
        )

        asyncio.run(duo.handle_duo_opt_in_callback(primary))
        assert session["duo_invite_open"] is True
        invite_markup = primary.message.answer.await_args.kwargs["reply_markup"]
        assert invite_markup.inline_keyboard[0][0].callback_data == "cr_duo_join_-42"

        asyncio.run(duo.handle_duo_opt_in_callback(second))
        assert session["drawer_ids"] == [1, 2]
        assert session["drawer_names"] == ["Первый", "Второй"]
        assert session["mode"] == "duo"
        assert "duo_invite_open" not in session
        assert persist.call_count == 2
    finally:
        crocodile.game_sessions.pop("-42", None)


def test_stale_legacy_duo_button_no_longer_allows_direct_join(monkeypatch):
    from games import crocodile
    from games import crocodile_duo_optin as duo

    crocodile.game_sessions["-42"] = {
        "drawer_id": 1,
        "drawer_name": "Первый",
    }
    persist = MagicMock()
    monkeypatch.setattr(duo, "_persist_regular_state", persist)
    callback = _callback("cr_duo_-42", 2, "Второй")

    try:
        asyncio.run(duo.handle_duo_opt_in_callback(callback))
        assert "drawer_ids" not in crocodile.game_sessions["-42"]
        persist.assert_not_called()
    finally:
        crocodile.game_sessions.pop("-42", None)


def test_duo_invite_open_is_persisted_until_join(monkeypatch):
    from games import crocodile_duo_optin as duo

    monkeypatch.setattr(
        duo,
        "_original_session_to_record",
        lambda chat_id, session: {"chat_id": chat_id, "drawer_id": session["drawer_id"]},
    )
    monkeypatch.setattr(
        duo,
        "_original_session_from_record",
        lambda record: (str(record["chat_id"]), {"drawer_id": record["drawer_id"]}),
    )
    session = {"drawer_id": 1, "duo_invite_open": True}

    record = duo._session_to_record_with_duo_opt_in("-42", session)
    chat_id, restored = duo._session_from_record_with_duo_opt_in(record)

    assert record["duo_invite_open"] is True
    assert chat_id == "-42"
    assert restored["duo_invite_open"] is True
