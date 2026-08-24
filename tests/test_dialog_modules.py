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


def test_talking_is_compatibility_facade_not_dialog_implementation():
    source = _source("AI/talking.py")
    tree = ast.parse(source)

    function_names = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert function_names == {"handle_bot_conversation", "process_general_message"}
    assert "AI.dialog.generation" in source
    assert "AI.dialog.model_commands" in source
    assert "AI.dialog.prompt_commands" in source
    assert "AI.dialog.serious_mode" in source
    assert "AI.dialog.settings" in source


def test_focused_dialog_modules_never_import_talking_facade():
    for path in (
        "AI/dialog/generation.py",
        "AI/dialog/model_commands.py",
        "AI/dialog/prompt_commands.py",
        "AI/dialog/serious_mode.py",
        "AI/dialog/settings.py",
        "AI/dialog/style.py",
    ):
        assert "AI.talking" not in _imported_modules(path)


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


def test_talking_compatibility_exports_remain_available():
    from AI import talking

    expected = (
        "generate_simple_response",
        "generate_response",
        "handle_bot_conversation",
        "handle_serious_mode_reply",
        "handle_switch_to_gemini",
        "handle_set_prompt_command",
        "update_chat_settings",
        "get_current_chat_prompt",
        "build_prompt_with_current_chat_prompt",
    )
    for name in expected:
        assert callable(getattr(talking, name))
