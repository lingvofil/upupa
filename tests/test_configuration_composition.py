import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests import test_smoke_imports

del test_smoke_imports

import core.loader as loader
from core import settings
from infrastructure.ai import clients


ROOT = Path(__file__).resolve().parents[1]


def test_core_loader_has_no_import_time_aiogram_construction():
    path = ROOT / "core" / "loader.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = []

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id in {"Bot", "Dispatcher"}:
                forbidden.append((value.func.id, node.lineno))

    assert not forbidden, f"aiogram resources must be built in app.bootstrap: {forbidden}"


def test_legacy_bot_proxy_delegates_after_configuration(monkeypatch):
    class FakeBot:
        marker = "configured"

    fake_bot = FakeBot()
    fake_dispatcher = object()
    monkeypatch.setattr(loader, "_bot", None)
    monkeypatch.setattr(loader, "_dispatcher", None)

    with pytest.raises(loader.AiogramResourceNotConfigured):
        loader.bot.unwrap()

    loader.configure_aiogram_components(
        bot_instance=fake_bot,
        dispatcher=fake_dispatcher,
    )

    assert loader.bot.marker == "configured"
    assert loader.dp.unwrap() is fake_dispatcher


def test_required_settings_validate_only_telegram_token(monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", None)

    with pytest.raises(settings.SettingsValidationError, match="API_TOKEN"):
        settings.validate_required_settings()


def test_settings_import_without_gemini_keys():
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GENERIC_API_KEY")
    }
    env["API_TOKEN"] = "123456789:AAFakeTokenForConfigurationTest_abcdef"
    env["PYTHONPATH"] = str(ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from core import settings; "
                "assert settings.GEMINI_KEYS_POOL == []; "
                "assert settings.PRIMARY_GEMINI_KEY is None; "
                "settings.validate_required_settings()"
            ),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_optional_provider_fails_only_when_requested(monkeypatch):
    monkeypatch.setattr(clients, "PRIMARY_GEMINI_KEY", None)

    with pytest.raises(
        clients.ProviderConfigurationError,
        match="GENERIC_API_KEY",
    ):
        clients._build_gemini_client()
