import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def _callback(data: str, user_id: int, chat_id: int = -42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, full_name=f"User {user_id}"),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
        answer=AsyncMock(),
    )


def _game(admin_id: int):
    return {
        "phase": "playing",
        "host_id": 101,
        "step": 1,
        "players": [
            (101, "Ведущий"),
            (202, "Текущий"),
            (303, "Другой участник"),
        ],
        "chain": [],
        "admin_id_for_test": admin_id,
    }


def test_skip_permission_allows_host_current_player_and_admin():
    from games import crocodile_telephone_skip_permissions as permissions

    game = _game(permissions.ADMIN_ID)

    assert permissions.can_skip_telephone_player(game, 101) is True
    assert permissions.can_skip_telephone_player(game, 202) is True
    assert permissions.can_skip_telephone_player(game, permissions.ADMIN_ID) is True


def test_skip_permission_denies_other_participant_and_outsider():
    from games import crocodile_telephone_skip_permissions as permissions

    game = _game(permissions.ADMIN_ID)

    assert permissions.can_skip_telephone_player(game, 303) is False
    assert permissions.can_skip_telephone_player(game, 404) is False


def test_direct_skip_blocks_non_current_participant():
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    downstream = AsyncMock()
    callback = _callback(f"ctel_skip_{cid}", 303)

    try:
        asyncio.run(
            permissions.telephone_callback_with_skip_permissions(
                callback,
                downstream,
            )
        )
        callback.answer.assert_awaited_once_with(
            "Пропустить может только ведущий или текущий игрок",
            show_alert=True,
        )
        downstream.assert_not_awaited()
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_direct_skip_delegates_for_host_current_and_admin():
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    downstream = AsyncMock(return_value="delegated")

    try:
        for user_id in (101, 202, permissions.ADMIN_ID):
            downstream.reset_mock()
            callback = _callback(f"ctel_skip_{cid}", user_id)
            result = asyncio.run(
                permissions.telephone_callback_with_skip_permissions(
                    callback,
                    downstream,
                )
            )
            assert result == "delegated"
            downstream.assert_awaited_once_with(callback)
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_direct_non_skip_delegates_once():
    from games import crocodile_telephone_skip_permissions as permissions

    callback = _callback("ctel_join_-42", 101)
    downstream = AsyncMock(return_value="delegated")

    result = asyncio.run(
        permissions.telephone_callback_with_skip_permissions(callback, downstream)
    )

    assert result == "delegated"
    downstream.assert_awaited_once_with(callback)


def test_menu_skip_blocks_non_current_participant():
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    downstream = AsyncMock()
    callback = _callback("cmenu_skip", 303)

    try:
        asyncio.run(permissions.menu_callback_with_skip_permissions(callback, downstream))
        callback.answer.assert_awaited_once_with(
            "Пропустить может только ведущий или текущий игрок",
            show_alert=True,
        )
        downstream.assert_not_awaited()
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_menu_skip_delegates_for_host_and_current_player():
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    downstream = AsyncMock(return_value="delegated")

    try:
        for user_id in (101, 202):
            downstream.reset_mock()
            callback = _callback("cmenu_skip", user_id)
            result = asyncio.run(
                permissions.menu_callback_with_skip_permissions(callback, downstream)
            )
            assert result == "delegated"
            downstream.assert_awaited_once_with(callback)
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_menu_skip_admin_can_override_without_being_participant(monkeypatch):
    from games import crocodile_telephone_skip_permissions as permissions

    cid = "-42"
    game = _game(permissions.ADMIN_ID)
    permissions.crocodile_modes.telephone_games[cid] = game
    downstream = AsyncMock()
    skip = AsyncMock()
    monkeypatch.setattr(permissions.crocodile_party_controls, "_skip_telephone", skip)
    callback = _callback("cmenu_skip", permissions.ADMIN_ID)

    try:
        asyncio.run(permissions.menu_callback_with_skip_permissions(callback, downstream))
        callback.answer.assert_awaited_once_with("Пропускаем (админ)")
        skip.assert_awaited_once_with(cid, game)
        downstream.assert_not_awaited()
    finally:
        permissions.crocodile_modes.telephone_games.pop(cid, None)


def test_menu_non_skip_delegates_once():
    from games import crocodile_telephone_skip_permissions as permissions

    callback = _callback("cmenu_duel", 101)
    downstream = AsyncMock(return_value="delegated")

    result = asyncio.run(
        permissions.menu_callback_with_skip_permissions(callback, downstream)
    )

    assert result == "delegated"
    downstream.assert_awaited_once_with(callback)


def test_telephone_callback_configurator_drives_stable_entrypoint():
    from games import crocodile_modes as modes

    callback = _callback("configured", 101)
    original = modes.get_telephone_callback_handler()
    configured = AsyncMock(return_value="configured-result")

    try:
        modes.configure_telephone_callback_handler(configured)
        result = asyncio.run(modes.handle_telephone_callback(callback))
    finally:
        modes.configure_telephone_callback_handler(original)

    assert result == "configured-result"
    configured.assert_awaited_once_with(callback)


def test_direct_skip_pipeline_is_explicitly_composed_in_runtime():
    permissions_source = (
        ROOT / "games" / "crocodile_telephone_skip_permissions.py"
    ).read_text(encoding="utf-8")
    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "_original_telephone_callback" not in permissions_source
    assert "crocodile_modes.handle_telephone_callback =" not in permissions_source
    assert (
        "async def telephone_callback_with_skip_permissions(callback, next_handler)"
        in permissions_source
    )
    assert "return await next_handler(callback)" in permissions_source

    modes_source = (ROOT / "games" / "crocodile_modes.py").read_text(encoding="utf-8")
    party_source = (ROOT / "games" / "crocodile_party_controls.py").read_text(
        encoding="utf-8"
    )

    assert "def get_telephone_callback_handler(" in modes_source
    assert "def configure_telephone_callback_handler(" in modes_source
    assert (
        "_original_handle_telephone_callback = "
        "crocodile_modes.get_default_telephone_callback_handler()"
        in party_source
    )
    assert (
        "crocodile_modes.configure_telephone_callback_handler("
        "handle_telephone_callback_resilient)"
        in party_source
    )

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
                    and target.attr == "handle_telephone_callback"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "crocodile_modes"
                ):
                    violations.append(f"{relative}:{node.lineno}")
    assert not violations, (
        "crocodile_modes.handle_telephone_callback нельзя заменять прямым "
        "присваиванием; используй configure_telephone_callback_handler(): "
        + ", ".join(violations)
    )

    party_install = runtime_source.index("party_controls.configure_crocodile_party_controls()")
    composition = "telephone_callback_handler = _compose_callback_handler("
    permissions_composition = runtime_source.index(composition, party_install)
    base = runtime_source.index(
        "party_controls.handle_telephone_callback_resilient,",
        permissions_composition,
    )
    permissions = runtime_source.index(
        "telephone_callback_with_skip_permissions,",
        base,
    )
    admin_composition = runtime_source.index(
        composition,
        permissions,
    )
    admin_wrapper = runtime_source.index(
        "handle_telephone_callback_with_admin,",
        admin_composition,
    )
    permissions_installer = runtime_source.index(
        "configure_crocodile_telephone_skip_permissions()",
        admin_wrapper,
    )
    roles_installer = runtime_source.index(
        "configure_crocodile_telephone_roles()",
        permissions_installer,
    )
    roles_composition = runtime_source.index(
        composition,
        roles_installer,
    )
    roles_wrapper = runtime_source.index(
        "handle_telephone_callback_with_roles,",
        roles_composition,
    )
    announcements_installer = runtime_source.index(
        "configure_crocodile_telephone_role_announcements()",
        roles_wrapper,
    )
    announcements_composition = runtime_source.index(
        composition,
        announcements_installer,
    )
    announcements_wrapper = runtime_source.index(
        "telephone_callback_with_role_announcement,",
        announcements_composition,
    )
    wiring = "crocodile_modes.configure_telephone_callback_handler("
    final_wiring = runtime_source.index(wiring, announcements_wrapper)

    roles_source = (ROOT / "games" / "crocodile_telephone_roles.py").read_text(
        encoding="utf-8"
    )
    announcements_source = (
        ROOT / "games" / "crocodile_telephone_role_announcements.py"
    ).read_text(encoding="utf-8")
    assert "_original_handle_telephone_callback" not in roles_source
    assert "_original_handle_telephone_callback" not in announcements_source
    assert "crocodile_modes.configure_telephone_callback_handler(" not in roles_source
    assert "crocodile_modes.configure_telephone_callback_handler(" not in announcements_source
    assert "handle_telephone_callback_with_roles(callback, next_handler)" in roles_source
    assert (
        "telephone_callback_with_role_announcement(callback, next_handler)"
        in announcements_source
    )

    assert runtime_source.count(wiring) == 1
    assert (
        party_install
        < permissions_composition
        < base
        < permissions
        < admin_composition
        < admin_wrapper
        < permissions_installer
        < roles_installer
        < roles_composition
        < roles_wrapper
        < announcements_installer
        < announcements_composition
        < announcements_wrapper
        < final_wiring
    )

    assert runtime_source.count(wiring) == 2
    assert (
        party_install
        < permissions_composition
        < base
        < permissions
        < permissions_wiring
        < admin_composition
        < admin_wrapper
        < admin_wiring
        < permissions_installer
        < roles_installer
        < announcements_installer
    )
