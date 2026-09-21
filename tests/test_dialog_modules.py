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


def test_poem_active_users_are_ranked_by_recent_activity():
    from AI.dialog.prompt_commands import _rank_active_poem_users

    users = {
        "1": {"weekly": 4, "daily": 4, "total": 100},
        "2": {"weekly": 9, "daily": 0, "total": 20},
        "3": {"weekly": 9, "daily": 3, "total": 10},
        "4": {"weekly": 0, "daily": 0, "total": 200},
    }

    assert _rank_active_poem_users(users, limit=4) == ["3", "2", "1", "4"]


def test_poem_dynamic_characters_use_active_chat_members(monkeypatch):
    import asyncio
    from AI.dialog import prompt_commands

    async def valid_users(chat_id):
        assert chat_id == "-1001"
        return {
            "11": {"weekly": 8, "daily": 2, "total": 100},
            "22": {"weekly": 7, "daily": 6, "total": 90},
            "33": {"weekly": 6, "daily": 1, "total": 80},
            "44": {"weekly": 5, "daily": 4, "total": 70},
            "55": {"weekly": 4, "daily": 3, "total": 60},
        }

    names = {
        11: "Света",
        22: "Алина",
        33: "Детектор",
        44: "Ольга",
        55: "Никита",
    }

    async def display_name(chat_id, user_id):
        assert chat_id == -1001
        return names[user_id]

    monkeypatch.setattr(prompt_commands, "get_valid_users", valid_users)
    monkeypatch.setattr(prompt_commands, "get_user_display_name", display_name)
    monkeypatch.setattr(prompt_commands.random, "sample", lambda values, k: list(values)[:k])

    characters = asyncio.run(prompt_commands._get_dynamic_poem_characters("-1001"))

    assert characters == "Света, Алина, Детектор, Ольга"


def test_poem_prompts_do_not_hardcode_legacy_chat_members():
    from prompts import PROMPT_PIROZHOK, PROMPT_PIROZHOK1, PROMPT_POROSHOK, PROMPT_POROSHOK1

    assert PROMPT_PIROZHOK1 is PROMPT_PIROZHOK
    assert PROMPT_POROSHOK1 is PROMPT_POROSHOK
    assert "анна" not in PROMPT_PIROZHOK1[0].lower()
    assert "анна" not in PROMPT_POROSHOK1[0].lower()
