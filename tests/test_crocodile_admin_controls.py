import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


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


def test_admin_bypasses_regular_stop_lock():
    from games import crocodile_admin_controls as admin

    downstream = lambda session, user_id, now=None: 123.0

    assert (
        admin.stop_lock_remaining_seconds_with_admin(
            {}, admin.ADMIN_ID, downstream
        )
        == 0.0
    )
    assert (
        admin.stop_lock_remaining_seconds_with_admin(
            {}, admin.ADMIN_ID + 1, downstream
        )
        == 123.0
    )


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
    downstream = AsyncMock()
    monkeypatch.setattr(admin.crocodile_modes, "_send_telephone_step", send_step)
    callback = _callback(f"ctel_start_{cid}", admin.ADMIN_ID)

    try:
        asyncio.run(admin.handle_telephone_callback_with_admin(callback, downstream))
        assert game["phase"] == "playing"
        callback.answer.assert_awaited_once_with("Поехали (админ)")
        send_step.assert_awaited_once_with(cid, game)
        downstream.assert_not_awaited()
    finally:
        admin.crocodile_modes.telephone_games.pop(cid, None)


def test_admin_can_cancel_and_skip_telephone(monkeypatch):
    from games import crocodile_admin_controls as admin

    cid = "-42"
    cancel_game = {"phase": "lobby", "host_id": admin.ADMIN_ID + 1, "players": []}
    admin.crocodile_modes.telephone_games[cid] = cancel_game
    cancel = AsyncMock()
    downstream = AsyncMock()
    monkeypatch.setattr(admin.crocodile_party_controls, "_cancel_telephone", cancel)

    callback = _callback(f"ctel_cancel_{cid}", admin.ADMIN_ID)
    asyncio.run(admin.handle_telephone_callback_with_admin(callback, downstream))
    cancel.assert_awaited_once_with(cid, cancel_game)
    downstream.assert_not_awaited()

    playing = {"phase": "playing", "host_id": admin.ADMIN_ID + 1, "players": []}
    admin.crocodile_modes.telephone_games[cid] = playing
    skip = AsyncMock()
    monkeypatch.setattr(admin.crocodile_party_controls, "_skip_telephone", skip)

    callback = _callback(f"ctel_skip_{cid}", admin.ADMIN_ID)
    try:
        asyncio.run(admin.handle_telephone_callback_with_admin(callback, downstream))
        skip.assert_awaited_once_with(cid, playing)
        downstream.assert_not_awaited()
    finally:
        admin.crocodile_modes.telephone_games.pop(cid, None)


def test_non_admin_telephone_delegates_once():
    from games import crocodile_admin_controls as admin

    downstream = AsyncMock(return_value="delegated")
    callback = _callback("ctel_start_-42", admin.ADMIN_ID + 1)

    result = asyncio.run(
        admin.handle_telephone_callback_with_admin(callback, downstream)
    )

    assert result == "delegated"
    downstream.assert_awaited_once_with(callback)


def test_admin_can_cancel_duel_without_being_initiator(monkeypatch):
    from games import crocodile_admin_controls as admin

    cid = "-42"
    duel = {"phase": "drawing", "host_id": admin.ADMIN_ID + 1, "artists": []}
    admin.crocodile_modes.duel_games[cid] = duel
    cancel = AsyncMock()
    downstream = AsyncMock()
    monkeypatch.setattr(admin.crocodile_party_controls, "_cancel_duel", cancel)
    callback = _callback(f"cduel_cancel_{cid}", admin.ADMIN_ID)

    try:
        asyncio.run(admin.handle_duel_callback_with_admin(callback, downstream))
        cancel.assert_awaited_once_with(cid, duel)
        downstream.assert_not_awaited()
    finally:
        admin.crocodile_modes.duel_games.pop(cid, None)


def test_non_admin_keeps_existing_duel_permissions():
    from games import crocodile_admin_controls as admin

    downstream = AsyncMock(return_value="delegated")
    callback = _callback("cduel_cancel_-42", admin.ADMIN_ID + 1)

    result = asyncio.run(admin.handle_duel_callback_with_admin(callback, downstream))

    assert result == "delegated"
    downstream.assert_awaited_once_with(callback)


def test_admin_unified_stop_can_end_reverse_round(monkeypatch):
    from games import crocodile_admin_controls as admin

    cid = "-42"
    admin.crocodile.game_sessions.pop(cid, None)
    admin.crocodile_modes.telephone_games.pop(cid, None)
    admin.crocodile_modes.duel_games.pop(cid, None)
    stop_reverse = AsyncMock(return_value=True)
    downstream = AsyncMock()
    monkeypatch.setattr(admin, "_stop_reverse_as_admin", stop_reverse)

    stopped, text = asyncio.run(
        admin.stop_active_party_with_admin(cid, admin.ADMIN_ID, downstream)
    )

    assert stopped is True
    assert text == "Раунд наоборот остановлен."
    stop_reverse.assert_awaited_once_with(cid)
    downstream.assert_not_awaited()


def test_non_admin_unified_stop_delegates_once():
    from games import crocodile_admin_controls as admin

    downstream = AsyncMock(return_value=(True, "delegated"))

    result = asyncio.run(
        admin.stop_active_party_with_admin("-42", admin.ADMIN_ID + 1, downstream)
    )

    assert result == (True, "delegated")
    downstream.assert_awaited_once_with("-42", admin.ADMIN_ID + 1)


def test_reverse_menu_exposes_admin_emergency_stop(monkeypatch):
    from games import crocodile_admin_controls as admin

    downstream = lambda _chat_id: InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="cmenu_refresh")],
            [InlineKeyboardButton(text="🏆 Рейтинги", callback_data="cmenu_ratings")],
            [InlineKeyboardButton(text="🖼 Галерея", callback_data="cmenu_gallery")],
        ]
    )
    monkeypatch.setattr(admin.crocodile_party_controls, "_reverse_active", lambda _cid: True)

    keyboard = admin.menu_keyboard_with_admin_emergency_stop(-42, downstream)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    emergency = next(button for button in buttons if button.callback_data == "cmenu_stop")

    assert emergency.text == "🛠 Стоп (админ)"


def test_admin_surrender_button_bypasses_reverse_timer(monkeypatch):
    from games import crocodile_admin_controls as admin

    stop_reverse = AsyncMock(return_value=True)
    downstream = AsyncMock()
    monkeypatch.setattr(admin, "_stop_reverse_as_admin", stop_reverse)
    callback = _callback("rcroc_stop_-42", admin.ADMIN_ID)

    asyncio.run(admin.reverse_callback_with_admin(callback, downstream))

    stop_reverse.assert_awaited_once_with("-42")
    callback.answer.assert_awaited_once_with("Остановлено администратором")
    downstream.assert_not_awaited()


def test_non_admin_reverse_callback_delegates_once():
    from games import crocodile_admin_controls as admin

    downstream = AsyncMock(return_value="delegated")
    callback = _callback("rcroc_stop_-42", admin.ADMIN_ID + 1)

    result = asyncio.run(admin.reverse_callback_with_admin(callback, downstream))

    assert result == "delegated"
    downstream.assert_awaited_once_with(callback)


def test_admin_controls_are_explicitly_composed_in_runtime():
    admin_source = (ROOT / "games" / "crocodile_admin_controls.py").read_text(
        encoding="utf-8"
    )
    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "_original_" not in admin_source
    for assignment in (
        "crocodile_controls.stop_lock_remaining_seconds =",
        "crocodile_modes.handle_telephone_callback =",
        "crocodile_modes.handle_duel_callback =",
        "crocodile_party_controls._stop_active_party =",
        "crocodile_party_controls.menu_keyboard =",
        "reverse.handle_callback =",
        "reverse_modes.handle_callback =",
    ):
        assert assignment not in admin_source

    assert "stop_lock_remaining_seconds_with_admin(" in admin_source
    assert "handle_telephone_callback_with_admin(callback, next_handler)" in admin_source
    assert "handle_duel_callback_with_admin(callback, next_handler)" in admin_source
    assert "stop_active_party_with_admin(" in admin_source
    assert "menu_keyboard_with_admin_emergency_stop(" in admin_source
    assert "reverse_callback_with_admin(callback, next_handler)" in admin_source
    assert "reverse_modes_callback_with_admin(callback, next_handler)" in admin_source

    permissions_assignment = (
        "crocodile_modes.handle_telephone_callback = _compose_callback_handler("
    )
    first_telephone = runtime_source.index(permissions_assignment)
    second_telephone = runtime_source.index(
        permissions_assignment,
        first_telephone + len(permissions_assignment),
    )
    admin_install = runtime_source.index("configure_crocodile_admin_controls()")

    assert runtime_source.count(permissions_assignment) == 2
    assert first_telephone < second_telephone < admin_install
    assert "handle_telephone_callback_with_admin," in runtime_source[second_telephone:admin_install]
    assert "crocodile_modes.handle_duel_callback = _compose_callback_handler(" in runtime_source
    assert "party_controls._stop_active_party = _compose_party_stop_handler(" in runtime_source
    assert "party_controls.menu_keyboard = _compose_menu_keyboard_handler(" in runtime_source
    assert "reverse.handle_callback = _compose_callback_handler(" in runtime_source
    assert "reverse_modes.handle_callback = _compose_callback_handler(" in runtime_source
    assert (
        "crocodile_controls.stop_lock_remaining_seconds = _compose_stop_lock_remaining_seconds("
        in runtime_source
    )
