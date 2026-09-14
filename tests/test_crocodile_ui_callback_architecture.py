import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ui_enhancements_do_not_replace_callback_handler():
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

    assert "handle_callback" not in assigned
    assert "_original_handle_callback" not in ui_source
    assert "handle_crocodile_callback_with_ui(callback, next_handler)" in ui_source


def test_runtime_composes_modes_then_duo_then_ui_in_single_callback_chain():
    runtime_source = _source("games/crocodile_runtime.py")
    assignment = (
        "crocodile.handle_callback = _compose_callback_handler(\n"
        "        raw_callback_handler,"
    )

    assert runtime_source.count(assignment) == 1
    callback_wiring = runtime_source.index(assignment)
    modes_router = runtime_source.index(
        "handle_regular_callback",
        callback_wiring,
    )
    duo_router = runtime_source.index(
        "duo_optin.handle_duo_opt_in_callback",
        modes_router,
    )
    ui_router = runtime_source.index(
        "handle_crocodile_callback_with_ui",
        duo_router,
    )
    ui_install = runtime_source.index(
        "configure_crocodile_ui_enhancements(\n"
        "        base_final_frame_handler=base_final_frame_handler,",
        ui_router,
    )

    assert callback_wiring < modes_router < duo_router < ui_router < ui_install