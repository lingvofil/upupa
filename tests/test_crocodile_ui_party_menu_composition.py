import ast
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _base_menu(_chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Обычный вдвоём", callback_data="cmenu_classic")],
            [InlineKeyboardButton(text="🖼 Галерея", callback_data="cmenu_gallery")],
        ]
    )


def test_party_menu_keeps_legacy_duo_ratings_admin_order(monkeypatch):
    from games import crocodile_admin_controls as admin
    from games import crocodile_duo_optin as duo
    from games import crocodile_runtime as runtime
    from games import crocodile_ui_enhancements as ui

    renderer = runtime._compose_party_menu_keyboard(
        _base_menu,
        duo.decorate_party_menu_without_default_duo,
        ui.decorate_party_menu_with_ratings,
    )
    monkeypatch.setattr(admin, "_original_menu_keyboard", renderer)
    monkeypatch.setattr(
        admin.crocodile_party_controls,
        "_reverse_active",
        lambda chat_id: True,
    )

    keyboard = admin.menu_keyboard_with_admin_emergency_stop(-42)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for button in buttons]

    assert callbacks == [
        "cmenu_classic",
        "cmenu_stop",
        "cmenu_ratings",
        "cmenu_gallery",
    ]
    assert buttons[0].text == "🎨 Обычный"


def test_ui_enhancements_do_not_replace_party_menu_keyboard():
    ui_source = _source("games/crocodile_ui_enhancements.py")
    tree = ast.parse(ui_source)
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
                and target.value.id == "crocodile_party_controls"
            ):
                assigned.append(target.attr)

    assert "menu_keyboard" not in assigned
    assert "_original_party_menu_keyboard" not in ui_source
    assert "menu_keyboard_with_ratings" not in ui_source
    assert "decorate_party_menu_with_ratings" in ui_source

    runtime_source = _source("games/crocodile_runtime.py")
    assignment = "party_controls.menu_keyboard = _compose_party_menu_keyboard("
    assert runtime_source.count(assignment) == 1
    wiring = runtime_source.index(assignment)
    duo = runtime_source.index(
        "duo_optin.decorate_party_menu_without_default_duo",
        wiring,
    )
    ratings = runtime_source.index("decorate_party_menu_with_ratings", duo)
    ui_install = runtime_source.index(
        "configure_crocodile_ui_enhancements(\n"
        "        base_final_frame_handler=base_final_frame_handler,",
        ratings,
    )
    admin_install = runtime_source.index("configure_crocodile_admin_controls()", ui_install)
    assert wiring < duo < ratings < ui_install < admin_install
