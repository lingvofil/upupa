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


def test_duo_keyboard_shows_artist_invite_before_join():
    from games import crocodile
    from games import crocodile_duo_optin as duo

    chat_id = -42
    crocodile.game_sessions[str(chat_id)] = {
        "drawer_id": 1,
        "drawer_name": "Первый",
    }

    try:
        keyboard = duo.decorate_game_keyboard_with_duo_opt_in(
            chat_id,
            _base_keyboard(chat_id),
        )
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
        keyboard = duo.decorate_game_keyboard_with_duo_opt_in(
            chat_id,
            _base_keyboard(chat_id),
        )
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


def test_duo_game_keyboard_composes_before_ui_clear_next(monkeypatch):
    from games import crocodile
    from games import crocodile_duo_optin as duo
    from games import crocodile_runtime as runtime
    from games import crocodile_ui_enhancements as ui

    chat_id = -42
    crocodile.game_sessions[str(chat_id)] = {
        "drawer_id": 1,
        "drawer_name": "Первый",
    }

    def base_keyboard(cid: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Другое", callback_data=f"cr_n_{cid}")],
                [InlineKeyboardButton(text="👥 Рисовать вдвоём", callback_data=f"cr_duo_{cid}")],
            ]
        )

    renderer = runtime._compose_game_keyboard(
        base_keyboard,
        duo.decorate_game_keyboard_with_duo_opt_in,
    )
    monkeypatch.setattr(ui, "_original_get_game_keyboard", renderer)

    try:
        keyboard = ui.get_game_keyboard_with_clear_next(chat_id)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for button in buttons if button.callback_data]
        next_button = next(
            button
            for button in buttons
            if button.callback_data == f"cr_n_{chat_id}"
        )

        assert next_button.text == "⏭ Следующее"
        assert "cr_duo_invite_-42" in callbacks
        assert "cr_duo_-42" not in callbacks
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
    monkeypatch.setattr(duo, "_base_game_keyboard", _base_keyboard)

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

        edited_markup = primary.message.edit_reply_markup.await_args.kwargs["reply_markup"]
        assert all(
            not str(button.callback_data or "").startswith("cr_duo_")
            for row in edited_markup.inline_keyboard
            for button in row
        )

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


def test_duo_invite_open_is_persisted_until_join():
    from games import crocodile_duo_optin as duo

    session = {"drawer_id": 1, "duo_invite_open": True}
    record = {"chat_id": "-42", "drawer_id": 1}

    record = duo.enrich_session_record_with_duo_opt_in("-42", session, record)
    chat_id, restored = duo.enrich_restored_session_with_duo_opt_in(
        record,
        "-42",
        {"drawer_id": 1},
    )

    assert record["duo_invite_open"] is True
    assert chat_id == "-42"
    assert restored["duo_invite_open"] is True


def test_duo_opt_in_and_party_metadata_compose_in_legacy_order():
    from games import crocodile_duo_optin as duo
    from games import crocodile_party_state as party_state
    from games import crocodile_runtime as runtime

    party_dependencies = party_state.crocodile_persistence_dependencies()
    record_enricher = runtime._compose_session_record_enrichers(
        duo.enrich_session_record_with_duo_opt_in,
        party_dependencies.enrich_session_record,
    )
    restored_enricher = runtime._compose_restored_session_enrichers(
        duo.enrich_restored_session_with_duo_opt_in,
        party_dependencies.enrich_restored_session,
    )
    session = {
        "drawer_id": 1,
        "drawer_ids": [1],
        "drawer_names": ["Первый"],
        "duo_invite_open": True,
    }

    record = record_enricher("-42", session, {"chat_id": "-42", "drawer_id": 1})
    chat_id, restored = restored_enricher(
        record,
        "-42",
        {"drawer_id": 1},
    )

    assert record["duo_invite_open"] is True
    assert record["drawer_ids"] == [1]
    assert record["drawer_names"] == ["Первый"]
    assert chat_id == "-42"
    assert restored["duo_invite_open"] is True
    assert restored["drawer_ids"] == [1]
    assert restored["drawer_names"] == ["Первый"]
