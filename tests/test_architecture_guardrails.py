"""Архитектурные ограничения для постепенного демонтажа legacy-связей."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "core"
INFRA_AI_DIR = ROOT / "infrastructure" / "ai"
DIALOG_DIR = ROOT / "AI" / "dialog"
HANDLERS_DIR = ROOT / "handlers"
FEATURES_DIR = ROOT / "features"
SERVICES_DIR = ROOT / "services"
GAMES_DIR = ROOT / "games"
BOOTSTRAP_FILE = ROOT / "app" / "bootstrap.py"
CHAT_SETTINGS_FILE = ROOT / "features" / "chat_settings.py"

FORBIDDEN_CORE_PREFIXES = (
    "config",
    "AI",
    "features",
    "services",
    "games",
    "handlers",
)

FORBIDDEN_AI_INFRA_PREFIXES = (
    "config",
    "AI",
    "features",
    "services",
    "games",
    "handlers",
)

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


def _collect_import_violations(
    directory: Path,
    forbidden_prefixes: tuple[str, ...],
    *,
    recursive: bool = False,
):
    violations = []
    paths = directory.rglob("*.py") if recursive else directory.glob("*.py")
    for path in sorted(paths):
        for module_name in _imports(path):
            for prefix in forbidden_prefixes:
                if _matches_prefix(module_name, prefix):
                    violations.append(f"{path.relative_to(ROOT)} -> {module_name}")
    return violations


def test_core_does_not_depend_on_legacy_or_upper_layers():
    violations = _collect_import_violations(CORE_DIR, FORBIDDEN_CORE_PREFIXES)

    assert not violations, (
        "core не должен зависеть от legacy/application слоёв. "
        "Найдены запрещённые зависимости:\n" + "\n".join(violations)
    )


def test_ai_infrastructure_does_not_depend_on_application_layer():
    violations = _collect_import_violations(
        INFRA_AI_DIR,
        FORBIDDEN_AI_INFRA_PREFIXES,
    )

    assert not violations, (
        "infrastructure.ai должен быть нижним provider-слоем. "
        "Найдены обратные зависимости:\n" + "\n".join(violations)
    )


def test_dialog_modules_do_not_depend_on_config_facade():
    violations = _collect_import_violations(DIALOG_DIR, ("config",))

    assert not violations, (
        "AI.dialog должен использовать canonical modules напрямую, без config.py. "
        "Найдены legacy-зависимости:\n" + "\n".join(violations)
    )


def test_handlers_and_features_do_not_depend_on_config_facade():
    violations = []
    for directory in (HANDLERS_DIR, FEATURES_DIR):
        violations.extend(
            _collect_import_violations(directory, ("config",), recursive=True)
        )

    assert not violations, (
        "handlers и features должны использовать canonical modules напрямую, без config.py. "
        "Найдены legacy-зависимости:\n" + "\n".join(violations)
    )


def test_services_and_games_do_not_depend_on_config_facade():
    violations = []
    for directory in (SERVICES_DIR, GAMES_DIR):
        violations.extend(
            _collect_import_violations(directory, ("config",), recursive=True)
        )

    assert not violations, (
        "services и games должны использовать canonical modules напрямую, без config.py. "
        "Найдены legacy-зависимости:\n" + "\n".join(violations)
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
