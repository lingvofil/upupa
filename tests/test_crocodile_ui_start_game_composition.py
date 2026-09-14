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


def test_start_game_composer_preserves_wrapper_order_and_result():
    from games import crocodile_runtime as runtime

    calls = []

    async def base(chat_id, user_id, user_full_name):
        calls.append(("base", chat_id, user_id, user_full_name))
        return "started"

    async def ui_wrapper(chat_id, user_id, user_full_name, next_handler):
        calls.append(("ui-before", chat_id, user_id, user_full_name))
        result = await next_handler(chat_id, user_id, user_full_name)
        calls.append(("ui-after", result))
        return result

    handler = runtime._compose_start_new_game(base, ui_wrapper)
    result = asyncio.run(handler(-42, 123, "Первый"))

    assert result == "started"
    assert calls == [
        ("ui-before", -42, 123, "Первый"),
        ("base", -42, 123, "Первый"),
        ("ui-after", "started"),
    ]


def test_ui_start_game_is_composed_only_in_runtime():
    ui_source = _source("games/crocodile_ui_enhancements.py")
    runtime_source = _source("games/crocodile_runtime.py")

    assigned = _assigned_attributes(ui_source, "crocodile")
    assert "start_new_game" not in assigned
    assert "_original_start_new_game" not in ui_source
    assert "start_new_game_with_instant_word" in ui_source
    assert "next_handler" in ui_source

    assignment = "crocodile.start_new_game = _compose_start_new_game("
    assert runtime_source.count(assignment) == 1

    controls_install = runtime_source.index("configure_crocodile_controls()")
    capture = runtime_source.index("base_start_new_game = crocodile.start_new_game")
    wiring = runtime_source.index(assignment)
    wrapper = runtime_source.index("start_new_game_with_instant_word", wiring)
    ui_install = runtime_source.index("configure_crocodile_ui_enhancements()", wrapper)

    assert controls_install < capture < wiring < wrapper < ui_install
