import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dialog_handler_has_no_runtime_monkeypatch():
    source = _source("handlers/dialog.py")
    tree = ast.parse(source)

    assert "install_into_random_reactions" not in source
    assert "talking.process_random_reactions" not in source
    assert "features.dialog_pipeline" in source

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                assert not isinstance(target, ast.Attribute), (
                    "dialog handler must not mutate imported module attributes at import time"
                )


def test_canonical_pipeline_does_not_call_legacy_composed_entrypoints():
    source = _source("features/dialog_pipeline.py")

    assert "random_reactions.process_random_reactions(" not in source
    assert "talking.process_general_message(" not in source
    assert "install_into_random_reactions" not in source
