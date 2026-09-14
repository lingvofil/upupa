import ast
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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


def test_controls_keyboard_pipeline_keeps_previous_duo_and_clear_next(monkeypatch):
    from games import crocodile
    from games import crocodile_controls as controls
    from games import crocodile_duo_optin as duo
    from games import crocodile_modes as modes
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
                [InlineKeyboardButton(text="🎨 Холст", url="https://example.com")],
                [
                    InlineKeyboardButton(text="👁 Слово", callback_data=f"cr_w_{cid}"),
                    InlineKeyboardButton(text="⏭ Другое", callback_data=f"cr_n_{cid}"),
                    InlineKeyboardButton(text="🛑 Стоп", callback_data=f"cr_stop_{cid}"),
                ],
            ]
        )

    controls_renderer = runtime._compose_game_keyboard(
        base_keyboard,
        controls.decorate_game_keyboard_with_previous,
    )
    monkeypatch.setattr(modes, "_original_get_game_keyboard", controls_renderer)

    try:
        pre_duo = modes.get_game_keyboard_with_duo(chat_id)
        keyboard = duo.decorate_game_keyboard_with_duo_opt_in(chat_id, pre_duo)
        keyboard = ui.decorate_game_keyboard_with_clear_next(chat_id, keyboard)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for button in buttons if button.callback_data]

        assert f"cr_p_{chat_id}" in callbacks
        assert f"cr_stop_{chat_id}" in callbacks
        assert f"cr_duo_invite_{chat_id}" in callbacks
        assert f"cr_duo_{chat_id}" not in callbacks
        next_button = next(
            button for button in buttons if button.callback_data == f"cr_n_{chat_id}"
        )
        assert next_button.text == "⏭ Следующее"
    finally:
        crocodile.game_sessions.pop(str(chat_id), None)


def test_controls_game_keyboard_is_composed_only_in_runtime_before_modes():
    controls_source = _source("games/crocodile_controls.py")
    runtime_source = _source("games/crocodile_runtime.py")

    assigned = _assigned_attributes(controls_source, "crocodile")
    assert "get_game_keyboard" not in assigned
    assert "_original_get_game_keyboard" not in controls_source
    assert "get_game_keyboard_with_previous" not in controls_source
    assert "decorate_game_keyboard_with_previous" in controls_source

    raw_capture = runtime_source.index("raw_game_keyboard = crocodile.get_game_keyboard")
    controls_install = runtime_source.index(
        "configure_crocodile_controls(base_start_new_game=base_start_new_game)"
    )
    controls_compose = runtime_source.index(
        "controls_game_keyboard = _compose_game_keyboard("
    )
    decorator = runtime_source.index(
        "decorate_game_keyboard_with_previous,",
        controls_compose,
    )
    controls_wiring = runtime_source.index(
        "crocodile.get_game_keyboard = controls_game_keyboard"
    )
    modes_install = runtime_source.index("configure_crocodile_modes()")
    post_modes_capture = runtime_source.index(
        "base_game_keyboard = crocodile.get_game_keyboard",
        modes_install,
    )
    final_wiring = runtime_source.index(
        "crocodile.get_game_keyboard = _compose_game_keyboard("
    )

    assert (
        raw_capture
        < controls_install
        < controls_compose
        < decorator
        < controls_wiring
        < modes_install
        < post_modes_capture
        < final_wiring
    )
