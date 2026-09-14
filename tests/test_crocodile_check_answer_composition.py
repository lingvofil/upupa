import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ui_answer_context_wraps_downstream_and_resets_afterwards():
    from games import crocodile
    from games import crocodile_ui_enhancements as ui

    chat_id = "-42"
    previous = crocodile.game_sessions.pop(chat_id, None)
    crocodile.game_sessions[chat_id] = {
        "drawer_id": 1,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
    }
    message = SimpleNamespace(chat=SimpleNamespace(id=-42))
    observed = []

    async def downstream(current_message):
        assert current_message is message
        observed.append(ui._final_like_context.get())
        return True

    try:
        result = asyncio.run(ui.check_answer_with_like_context(message, downstream))
    finally:
        crocodile.game_sessions.pop(chat_id, None)
        if previous is not None:
            crocodile.game_sessions[chat_id] = previous

    assert result is True
    assert observed == [
        {"chat_id": chat_id, "artists": [(1, "Первый"), (2, "Второй")]}
    ]
    assert ui._final_like_context.get() is None


def test_ui_does_not_own_check_answer_entrypoint():
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
                and target.value.id == "crocodile"
            ):
                assigned.append(target.attr)

    assert "check_answer" not in assigned
    assert "_original_check_answer" not in ui_source
    assert "check_answer_with_like_context(message, next_handler)" in ui_source

    runtime_source = _source("games/crocodile_runtime.py")
    assignment = "crocodile.check_answer = _compose_check_answer("
    assert runtime_source.count(assignment) == 1
    modes_install = runtime_source.index("configure_crocodile_modes()")
    base_capture = runtime_source.index("base_check_answer = crocodile.check_answer")
    wiring = runtime_source.index(assignment)
    wrapper = runtime_source.index("check_answer_with_like_context,", wiring)
    ui_install = runtime_source.index("configure_crocodile_ui_enhancements()")
    assert modes_install < base_capture < wiring < wrapper < ui_install
