"""R9 guardrails for retired AI/provider compatibility import paths."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_MODULES = {
    "AI.talking",
    "AI.wrapper",
    "AI.gigachat_client",
    "core.ai_clients",
}
REMOVED_PATHS = {
    "AI/talking.py",
    "AI/wrapper.py",
    "AI/gigachat_client.py",
    "core/ai_clients.py",
}
SOURCE_DIRS = (
    "app",
    "AI",
    "core",
    "infrastructure",
    "features",
    "services",
    "games",
    "handlers",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _source_files():
    for directory in SOURCE_DIRS:
        yield from (ROOT / directory).rglob("*.py")


def test_retired_facade_files_are_gone():
    for relative_path in REMOVED_PATHS:
        assert not (ROOT / relative_path).exists(), relative_path


def test_production_code_cannot_import_retired_facades():
    violations = []
    for path in _source_files():
        for module in _imports(path):
            if module in FORBIDDEN_MODULES:
                violations.append(f"{path.relative_to(ROOT)} -> {module}")

    assert not violations, "Retired compatibility imports returned:\n" + "\n".join(violations)
