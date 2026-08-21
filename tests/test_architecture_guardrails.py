"""Архитектурные ограничения для постепенного демонтажа legacy-связей."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"

# core.ai_clients -> AI.* пока остаётся известным долгом. На этом этапе
# запрещаем только новые обратные зависимости core от прикладных слоёв
# и возврат к compatibility-фасаду config.py.
FORBIDDEN_CORE_PREFIXES = ("config", "features", "services", "games", "handlers")


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _matches_prefix(module_name: str, prefix: str) -> bool:
    return module_name == prefix or module_name.startswith(prefix + ".")


def test_core_does_not_depend_on_legacy_or_upper_layers():
    violations = []

    for path in sorted(CORE_DIR.glob("*.py")):
        for module_name in _imports(path):
            for prefix in FORBIDDEN_CORE_PREFIXES:
                if _matches_prefix(module_name, prefix):
                    violations.append(f"{path.relative_to(ROOT)} -> {module_name}")

    assert not violations, (
        "core должен оставаться нижним инфраструктурным слоем. "
        "Найдены запрещённые зависимости:\n" + "\n".join(violations)
    )
