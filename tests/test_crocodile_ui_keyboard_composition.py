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


def test_end_game_keyboard_composition_preserves_likes_and_order():
    from games import crocodile_runtime as runtime

    seen = []

    def base(likes=0):
        seen.append(("base", likes))
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"❤️ {likes}", callback_data="btn_like")]
            ]
        )

    def decorate(likes, keyboard):
        seen.append(("decorate", likes))
        button = keyboard.inline_keyboard[0][0]
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=button.text, callback_data="decorated")]
            ]
        )

    renderer = runtime._compose_end_game_keyboard(base, decorate)
    keyboard = renderer(7)

    assert seen == [("base", 7), ("decorate", 7)]
    assert keyboard.inline_keyboard[0][0].text == "❤️ 7"
    assert keyboard.inline_keyboard[0][0].callback_data == "decorated"


def test_ui_keyboard_entrypoints_are_composed_only_in_runtime():
    ui_source = _source("games/crocodile_ui_enhancements.py")
    runtime_source = _source("games/crocodile_runtime.py")

    assigned = _assigned_attributes(ui_source, "crocodile")
    assert "get_game_keyboard" not in assigned
    assert "get_end_game_keyboard" not in assigned
    assert "_original_get_game_keyboard" not in ui_source
    assert "_original_get_end_game_keyboard" not in ui_source
    assert "get_game_keyboard_with_clear_next" not in ui_source
    assert "get_end_game_keyboard_with_attribution" not in ui_source
    assert "decorate_game_keyboard_with_clear_next" in ui_source
    assert "decorate_end_game_keyboard_with_attribution" in ui_source

    game_assignment = "crocodile.get_game_keyboard = _compose_game_keyboard("
    end_assignment = "crocodile.get_end_game_keyboard = _compose_end_game_keyboard("
    assert runtime_source.count(game_assignment) == 1
    assert runtime_source.count(end_assignment) == 1

    game_wiring = runtime_source.index(game_assignment)
    duo = runtime_source.index(
        "duo_optin.decorate_game_keyboard_with_duo_opt_in",
        game_wiring,
    )
    clear_next = runtime_source.index("decorate_game_keyboard_with_clear_next", duo)
    end_wiring = runtime_source.index(end_assignment, clear_next)
    attribution = runtime_source.index(
        "decorate_end_game_keyboard_with_attribution",
        end_wiring,
    )
    ui_install = runtime_source.index(
        "configure_crocodile_ui_enhancements(\n"
        "        base_final_frame_handler=base_final_frame_handler,",
        attribution,
    )

    assert game_wiring < duo < clear_next < end_wiring < attribution < ui_install
