import ast
from pathlib import Path

from tests import test_smoke_imports  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _imported_modules(path: str) -> set[str]:
    tree = ast.parse(_source(path), filename=path)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_focused_dialog_modules_are_self_contained():
    for path in (
        "AI/dialog/generation.py",
        "AI/dialog/model_commands.py",
        "AI/dialog/prompt_commands.py",
        "AI/dialog/serious_mode.py",
        "AI/dialog/settings.py",
        "AI/dialog/style.py",
    ):
        imports = _imported_modules(path)
        assert "AI.talking" not in imports


def test_dialog_settings_preserve_legacy_defaults():
    from AI.dialog import settings

    chat_id = "r7-test-defaults"
    settings.chat_settings.pop(chat_id, None)
    settings.update_chat_settings(chat_id)

    assert settings.chat_settings[chat_id]["dialog_enabled"] is True
    assert settings.chat_settings[chat_id]["reactions_enabled"] is True
    assert settings.chat_settings[chat_id]["prompt_name"] == "летописец"
    assert settings.chat_settings[chat_id]["prompt_source"] == "daily"
    assert settings.chat_settings[chat_id]["active_model"] == "gemini"

    settings.chat_settings.pop(chat_id, None)
