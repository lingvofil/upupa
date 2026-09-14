import ast
import asyncio
from pathlib import Path

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


def test_start_game_composer_preserves_controls_then_ui_order():
    from games import crocodile_runtime as runtime

    calls = []

    async def base(chat_id, user_id, user_full_name):
        calls.append(("base", chat_id, user_id, user_full_name))
        return "base-result"

    async def controls_wrapper(chat_id, user_id, user_full_name, next_handler):
        calls.append(("controls-before", chat_id, user_id, user_full_name))
        await next_handler(chat_id, user_id, user_full_name)
        calls.append(("controls-after", chat_id))
        return True

    async def ui_wrapper(chat_id, user_id, user_full_name, next_handler):
        calls.append(("ui-before", chat_id, user_id, user_full_name))
        result = await next_handler(chat_id, user_id, user_full_name)
        calls.append(("ui-after", result))
        return result

    handler = runtime._compose_start_new_game(base, controls_wrapper, ui_wrapper)
    result = asyncio.run(handler(-42, 123, "Первый"))

    assert result is True
    assert calls == [
        ("ui-before", -42, 123, "Первый"),
        ("controls-before", -42, 123, "Первый"),
        ("base", -42, 123, "Первый"),
        ("controls-after", -42),
        ("ui-after", True),
    ]


def test_start_game_is_composed_only_in_runtime():
    controls_source = _source("games/crocodile_controls.py")
    ui_source = _source("games/crocodile_ui_enhancements.py")
    runtime_source = _source("games/crocodile_runtime.py")

    controls_assigned = _assigned_attributes(controls_source, "crocodile")
    ui_assigned = _assigned_attributes(ui_source, "crocodile")
    assert "start_new_game" not in controls_assigned
    assert "start_new_game" not in ui_assigned
    assert "_original_start_new_game" not in controls_source
    assert "_original_start_new_game" not in ui_source
    assert "start_new_game_with_controls" in controls_source
    assert "next_handler" in controls_source
    assert "start_new_game_with_instant_word" in ui_source
    assert "next_handler" in ui_source

    assignment = "crocodile.start_new_game = _compose_start_new_game("
    assert runtime_source.count(assignment) == 1

    capture = runtime_source.index("base_start_new_game = crocodile.start_new_game")
    controls_install = runtime_source.index(
        "configure_crocodile_controls(base_start_new_game=base_start_new_game)"
    )
    wiring = runtime_source.index(assignment)
    controls_wrapper = runtime_source.index("start_new_game_with_controls,", wiring)
    ui_wrapper = runtime_source.index("start_new_game_with_instant_word,", controls_wrapper)
    ui_install = runtime_source.index("configure_crocodile_ui_enhancements()", ui_wrapper)

    assert capture < controls_install < wiring < controls_wrapper < ui_wrapper < ui_install
