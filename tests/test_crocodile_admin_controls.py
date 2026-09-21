import ast
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
    assert "_configured = False" not in admin_source
    assert "def configure_crocodile_admin_controls(" not in admin_source
    assert "configure_crocodile_admin_controls(" not in runtime_source
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

    telephone_wiring = "crocodile_modes.configure_telephone_callback_handler("
    composition = "telephone_callback_handler = _compose_callback_handler("
    party_install = runtime_source.index("party_controls.configure_crocodile_party_controls()")
    permissions_composition = runtime_source.index(composition, party_install)
    admin_composition = runtime_source.index(
        composition,
        permissions_composition + len(composition),
    )
    admin_wrapper = runtime_source.index(
        "handle_telephone_callback_with_admin,",
        admin_composition,
    )
    roles_install = runtime_source.index(
        "configure_crocodile_telephone_roles()",
        admin_wrapper,
    )
    roles_composition = runtime_source.index(composition, roles_install)
    roles_wrapper = runtime_source.index(
        "handle_telephone_callback_with_roles,",
        roles_composition,
    )
    announcements_composition = runtime_source.index(
        composition,
        roles_wrapper,
    )
    announcements_wrapper = runtime_source.index(
        "telephone_callback_with_role_announcement,",
        announcements_composition,
    )
    telephone_install = runtime_source.index(telephone_wiring, announcements_wrapper)

    assert runtime_source.count(telephone_wiring) == 1
    assert (
        permissions_composition
        < admin_composition
        < admin_wrapper
        < roles_install
        < roles_composition
        < roles_wrapper
        < announcements_composition
        < announcements_wrapper
        < telephone_install
    )
    assert "crocodile_modes.handle_telephone_callback =" not in runtime_source
    duel_wiring = "crocodile_modes.configure_duel_callback_handler("
    assert runtime_source.count(duel_wiring) == 1
    assert "crocodile_modes.handle_duel_callback =" not in runtime_source
    party_stop_wiring = "party_controls.configure_stop_active_party_handler("
    assert runtime_source.count(party_stop_wiring) == 1
    assert "party_controls._stop_active_party =" not in runtime_source
    menu_keyboard_wiring = "party_controls.configure_menu_keyboard_renderer("
    assert runtime_source.count(menu_keyboard_wiring) == 2
    assert "party_controls.menu_keyboard =" not in runtime_source
    reverse_wiring = "reverse.configure_callback_handler("
    assert runtime_source.count(reverse_wiring) == 1
    assert "reverse.handle_callback =" not in runtime_source
    reverse_modes_wiring = "reverse_modes.configure_callback_handler("
    assert runtime_source.count(reverse_modes_wiring) == 1
    assert "reverse_modes.handle_callback =" not in runtime_source
    violations = []
    for path in sorted((ROOT / "games").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "stop_lock_remaining_seconds"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "crocodile_controls"
                ):
                    violations.append(f"{relative}:{node.lineno}")
    assert not violations, (
        "crocodile_controls.stop_lock_remaining_seconds нельзя заменять прямым "
        "присваиванием; используй configure_stop_lock_remaining_seconds_handler(): "
        + ", ".join(violations)
    )

    stop_lock_wiring = "crocodile_controls.configure_stop_lock_remaining_seconds_handler("
    assert runtime_source.count(stop_lock_wiring) == 1
    assert "crocodile_controls.stop_lock_remaining_seconds =" not in runtime_source

    controls_source = (ROOT / "games" / "crocodile_controls.py").read_text(
        encoding="utf-8"
    )
    assert "def get_default_stop_lock_remaining_seconds_handler(" in controls_source
    assert "def get_stop_lock_remaining_seconds_handler(" in controls_source
    assert "def configure_stop_lock_remaining_seconds_handler(" in controls_source

    party_controls_source = (ROOT / "games" / "crocodile_party_controls.py").read_text(
        encoding="utf-8"
    )
    assert "def get_default_stop_active_party_handler(" in party_controls_source
    assert "def get_stop_active_party_handler(" in party_controls_source
    assert "def configure_stop_active_party_handler(" in party_controls_source
    assert "def get_default_menu_keyboard_renderer(" in party_controls_source
    assert "def get_menu_keyboard_renderer(" in party_controls_source
    assert "def configure_menu_keyboard_renderer(" in party_controls_source

    reverse_source = (ROOT / "games" / "reverse_crocodile.py").read_text(
        encoding="utf-8"
    )
    assert "def get_default_callback_handler(" in reverse_source
    assert "def get_callback_handler(" in reverse_source
    assert "def configure_callback_handler(" in reverse_source

    reverse_modes_source = (
        ROOT / "games" / "reverse_crocodile_modes.py"
    ).read_text(encoding="utf-8")
    assert "def get_default_callback_handler(" in reverse_modes_source
    assert "def get_callback_handler(" in reverse_modes_source
    assert "def configure_callback_handler(" in reverse_modes_source
