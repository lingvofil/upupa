import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assigned_attributes(source: str, module_name: str) -> list[str]:
    tree = ast.parse(source)
    assigned = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == module_name
            ):
                assigned.append(target.attr)
    return assigned


def _callback(data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=101, full_name="Ведущий"),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-42),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def test_party_menu_callback_chain_delegates_unowned_action_once():
    from games import crocodile_runtime as runtime
    from games import crocodile_telephone_skip_permissions as permissions
    from games import crocodile_ui_enhancements as ui

    callback = _callback("cmenu_duel")
    base = AsyncMock(return_value="handled-by-base")
    handler = runtime._compose_callback_handler(
        base,
        ui.handle_party_menu_callback_with_ratings,
        permissions.menu_callback_with_skip_permissions,
    )

    result = asyncio.run(handler(callback))

    assert result == "handled-by-base"
    base.assert_awaited_once_with(callback)


def test_party_menu_callback_chain_keeps_ratings_in_ui_layer():
    from games import crocodile_runtime as runtime
    from games import crocodile_telephone_skip_permissions as permissions
    from games import crocodile_ui_enhancements as ui

    callback = _callback("cmenu_ratings")
    base = AsyncMock()
    handler = runtime._compose_callback_handler(
        base,
        ui.handle_party_menu_callback_with_ratings,
        permissions.menu_callback_with_skip_permissions,
    )

    asyncio.run(handler(callback))

    callback.answer.assert_awaited_once_with()
    callback.message.edit_text.assert_awaited_once()
    base.assert_not_awaited()


def test_party_menu_callback_is_composed_only_in_runtime():
    ui_source = _source("games/crocodile_ui_enhancements.py")
    skip_source = _source("games/crocodile_telephone_skip_permissions.py")
    runtime_source = _source("games/crocodile_runtime.py")

    assert "handle_menu_callback" not in _assigned_attributes(
        ui_source,
        "crocodile_party_controls",
    )
    assert "_original_party_menu_callback" not in ui_source
    assert "handle_party_menu_callback_with_ratings(callback, next_handler)" in ui_source

    assert "handle_menu_callback" not in _assigned_attributes(
        skip_source,
        "crocodile_party_controls",
    )
    assert "_original_menu_callback" not in skip_source
    assert "menu_callback_with_skip_permissions(callback, next_handler)" in skip_source

    assignment = "party_controls.handle_menu_callback = _compose_callback_handler("
    assert runtime_source.count(assignment) == 1
    capture = runtime_source.index(
        "base_party_menu_handler = party_controls.handle_menu_callback"
    )
    wiring = runtime_source.index(assignment)
    ui_router = runtime_source.index(
        "handle_party_menu_callback_with_ratings",
        wiring,
    )
    skip_router = runtime_source.index(
        "menu_callback_with_skip_permissions",
        ui_router,
    )
    ui_install = runtime_source.index("configure_crocodile_ui_enhancements()", skip_router)
    skip_install = runtime_source.index(
        "configure_crocodile_telephone_skip_permissions()",
        ui_install,
    )

    assert capture < wiring < ui_router < skip_router < ui_install < skip_install
