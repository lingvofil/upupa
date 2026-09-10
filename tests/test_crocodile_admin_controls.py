import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _callback(data: str, user_id: int):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, full_name="Админ"),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-42),
            answer=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def test_admin_bypasses_regular_stop_lock(monkeypatch):
    from games import crocodile_admin_controls as admin

    monkeypatch.setattr(
        admin,
        "_original_stop_lock_remaining_seconds",
        lambda session, user_id, now=None: 123.0,
    )

    assert admin.stop_lock_remaining_seconds_with_admin({}, admin.ADMIN_ID) == 0.0
    assert admin.stop_lock_remaining_seconds_with_admin({}, admin.ADMIN_ID + 1) == 123.0


def test_admin_can_start_telephone_without_being_host(monkeypatch):
    from games import crocodile_admin_controls as admin

    cid = "-42"
    game = {
        "phase": "lobby",
        "host_id": admin.ADMIN_ID + 100,
        "players": [
            (admin.ADMIN_ID + 100, "Первый"),
            (admin.ADMIN_ID + 101, "Второй"),
            (admin.ADMIN_ID + 102, "Третий"),
        ],
    }
    admin.crocodile_modes.telephone_games[cid] = game
    send_step = AsyncMock()
    monkeypatch.setattr(admin.crocodile_modes, "_send_telephone_step", send_step)
    callback = _callback(f"ctel_start_{cid}", admin.ADMIN_ID)

    try:
        asyncio.run(admin.handle_telephone_callback_with_admin(callback))
        assert game["phase"] == "playing"
        callback.answer.assert_awaited_once_with("Поехали (админ)")
        send_step.assert_awaited_once_with(cid, game)
    finally:
        admin.crocodile_modes.telephone_games.pop(cid, None)


def test_admin_can_cancel_and_skip_telephone(monkeypatch):
    from games import crocodile_admin_controls as admin

    cid = "-42"
    cancel_game = {"phase": "lobby", "host_id": admin.ADMIN_ID + 1, "players": []}
    admin.crocodile_modes.telephone_games[cid] = cancel_game
    cancel = AsyncMock()
    monkeypatch.setattr(admin.crocodile_party_controls, "_cancel_telephone", cancel)

    callback = _callback(f"ctel_cancel_{cid}", admin.ADMIN_ID)
    asyncio.run(admin.handle_telephone_callback_with_admin(callback))
    cancel.assert_awaited_once_with(cid, cancel_game)

    playing = {"phase": "playing", "host_id": admin.ADMIN_ID + 1, "players": []}
    admin.crocodile_modes.telephone_games[cid] = playing
    skip = AsyncMock()
    monkeypatch.setattr(admin.crocodile_party_controls, "_skip_telephone", skip)

    callback = _callback(f"ctel_skip_{cid}", admin.ADMIN_ID)
    try:
        asyncio.run(admin.handle_telephone_callback_with_admin(callback))
        skip.assert_awaited_once_with(cid, playing)
    finally:
        admin.crocodile_modes.telephone_games.pop(cid, None)


def test_admin_can_cancel_duel_without_being_initiator(monkeypatch):
    from games import crocodile_admin_controls as admin

    cid = "-42"
    duel = {"phase": "drawing", "host_id": admin.ADMIN_ID + 1, "artists": []}
    admin.crocodile_modes.duel_games[cid] = duel
    cancel = AsyncMock()
    monkeypatch.setattr(admin.crocodile_party_controls, "_cancel_duel", cancel)
    callback = _callback(f"cduel_cancel_{cid}", admin.ADMIN_ID)

    try:
        asyncio.run(admin.handle_duel_callback_with_admin(callback))
        cancel.assert_awaited_once_with(cid, duel)
    finally:
        admin.crocodile_modes.duel_games.pop(cid, None)


def test_non_admin_keeps_existing_duel_permissions(monkeypatch):
    from games import crocodile_admin_controls as admin

    original = AsyncMock(return_value="delegated")
    monkeypatch.setattr(admin, "_original_handle_duel_callback", original)
    callback = _callback("cduel_cancel_-42", admin.ADMIN_ID + 1)

    result = asyncio.run(admin.handle_duel_callback_with_admin(callback))

    assert result == "delegated"
    original.assert_awaited_once_with(callback)


def test_admin_unified_stop_can_end_reverse_round(monkeypatch):
    from games import crocodile_admin_controls as admin

    cid = "-42"
    admin.crocodile.game_sessions.pop(cid, None)
    admin.crocodile_modes.telephone_games.pop(cid, None)
    admin.crocodile_modes.duel_games.pop(cid, None)
    stop_reverse = AsyncMock(return_value=True)
    monkeypatch.setattr(admin, "_stop_reverse_as_admin", stop_reverse)

    stopped, text = asyncio.run(
        admin.stop_active_party_with_admin(cid, admin.ADMIN_ID)
    )

    assert stopped is True
    assert text == "Раунд наоборот остановлен."
    stop_reverse.assert_awaited_once_with(cid)


def test_reverse_menu_exposes_admin_emergency_stop(monkeypatch):
    from games import crocodile_admin_controls as admin

    monkeypatch.setattr(
        admin,
        "_original_menu_keyboard",
        lambda _chat_id: InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="cmenu_refresh")],
                [InlineKeyboardButton(text="🏆 Рейтинги", callback_data="cmenu_ratings")],
                [InlineKeyboardButton(text="🖼 Галерея", callback_data="cmenu_gallery")],
            ]
        ),
    )
    monkeypatch.setattr(admin.crocodile_party_controls, "_reverse_active", lambda _cid: True)

    keyboard = admin.menu_keyboard_with_admin_emergency_stop(-42)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    emergency = next(button for button in buttons if button.callback_data == "cmenu_stop")

    assert emergency.text == "🛠 Стоп (админ)"


def test_admin_surrender_button_bypasses_reverse_timer(monkeypatch):
    from games import crocodile_admin_controls as admin

    stop_reverse = AsyncMock(return_value=True)
    monkeypatch.setattr(admin, "_stop_reverse_as_admin", stop_reverse)
    callback = _callback("rcroc_stop_-42", admin.ADMIN_ID)

    asyncio.run(admin.reverse_callback_with_admin(callback))

    stop_reverse.assert_awaited_once_with("-42")
    callback.answer.assert_awaited_once_with("Остановлено администратором")
