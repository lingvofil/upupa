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
    renderer = runtime._compose_menu_keyboard_handler(
        renderer,
        admin.menu_keyboard_with_admin_emergency_stop,
    )
    monkeypatch.setattr(
        admin.crocodile_party_controls,
        "_reverse_active",
        lambda chat_id: True,
    )

    keyboard = renderer(-42)
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
    wiring_entrypoint = "party_controls.configure_menu_keyboard_renderer("
    assert runtime_source.count(wiring_entrypoint) == 2
    assert "party_controls.menu_keyboard =" not in runtime_source
    composition = runtime_source.index(
        "party_menu_renderer = _compose_party_menu_keyboard("
    )
    base = runtime_source.index(
        "party_controls.get_default_menu_keyboard_renderer()",
        composition,
    )
    duo = runtime_source.index(
        "duo_optin.decorate_party_menu_without_default_duo",
        base,
    )
    ratings = runtime_source.index("decorate_party_menu_with_ratings", duo)
    wiring = runtime_source.index(wiring_entrypoint, ratings)
    ui_install = runtime_source.index(
        "configure_crocodile_ui_enhancements()",
        wiring,
    )
    admin_composition = runtime_source.index(
        "party_menu_renderer = _compose_menu_keyboard_handler(",
        ui_install,
    )
    admin_wrapper = runtime_source.index(
        "menu_keyboard_with_admin_emergency_stop,",
        admin_composition,
    )
    admin_wiring = runtime_source.index(
        wiring_entrypoint,
        admin_wrapper,
    )
    mentions_install = runtime_source.index(
        "configure_crocodile_telephone_mentions()",
        admin_wiring,
    )
    admin_source = _source("games/crocodile_admin_controls.py")
    assert "_configured = False" not in admin_source
    assert "def configure_crocodile_admin_controls(" not in admin_source
    assert "configure_crocodile_admin_controls(" not in runtime_source
    assert (
        composition
        < base
        < duo
        < ratings
        < wiring
        < ui_install
        < admin_composition
        < admin_wrapper
        < admin_wiring
        < mentions_install
    )
