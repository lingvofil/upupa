"""Архитектурные ограничения для постепенного демонтажа legacy-связей."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
BOOTSTRAP_FILE = ROOT / "app" / "bootstrap.py"
CHAT_SETTINGS_FILE = ROOT / "features" / "chat_settings.py"

# core.ai_clients -> AI.* пока остаётся известным долгом. На этом этапе
# запрещаем только новые обратные зависимости core от прикладных слоёв
# и возврат к compatibility-фасаду config.py.
FORBIDDEN_CORE_PREFIXES = ("config", "features", "services", "games", "handlers")

# Composition root имеет право знать обо всех слоях, но прикладные модули должны
# загружаться только во время startup. Иначе простой import app.bootstrap снова
# начнёт запускать monkeypatch/load side effects и сделает поведение зависимым от
# порядка импортов.
FORBIDDEN_BOOTSTRAP_TOP_LEVEL_PREFIXES = (
    "AI",
    "features",
    "services",
    "games",
    "handlers",
)


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _top_level_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _top_level_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name):
            yield func.id
        elif isinstance(func, ast.Attribute):
            yield func.attr


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


def test_bootstrap_defers_application_layer_imports_until_startup():
    violations = []

    for module_name in _top_level_imports(BOOTSTRAP_FILE):
        for prefix in FORBIDDEN_BOOTSTRAP_TOP_LEVEL_PREFIXES:
            if _matches_prefix(module_name, prefix):
                violations.append(module_name)

    assert not violations, (
        "app.bootstrap не должен загружать прикладные модули на import-time. "
        "Перенеси эти импорты в startup/configuration-функции:\n"
        + "\n".join(violations)
    )


def test_chat_settings_does_not_load_persistent_state_at_import_time():
    forbidden_calls = {"load_chat_settings", "load_chats", "load_chat_state"}
    violations = sorted(forbidden_calls.intersection(_top_level_calls(CHAT_SETTINGS_FILE)))

    assert not violations, (
        "features.chat_settings не должен читать файлы при импорте; "
        "загрузка состояния принадлежит application startup: " + ", ".join(violations)
    )
