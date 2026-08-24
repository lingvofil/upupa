"""Regression tests for the AI provider infrastructure boundary."""

import ast
import threading
from pathlib import Path

from tests import test_smoke_imports  # noqa: F401  (env + mocks)

import infrastructure.ai.clients as provider_clients
from infrastructure.ai.clients import LazyResource


ROOT = Path(__file__).resolve().parents[1]
CLIENTS_FILE = ROOT / "infrastructure" / "ai" / "clients.py"


def _call_name(call: ast.Call) -> str:
    parts = []
    node = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_provider_client_exports_are_lazy_resources():
    for name in (
        "gemini_client",
        "groq_ai",
        "model",
        "gigachat_model",
        "gigachat",
        "openrouter_ai",
        "siliconflow_ai",
    ):
        assert isinstance(getattr(provider_clients, name), LazyResource)


def test_lazy_resource_constructs_exactly_once_under_concurrency():
    calls = []
    factory_entered = threading.Event()
    allow_factory_to_finish = threading.Event()

    class Resource:
        value = "ok"

    def factory():
        calls.append("build")
        factory_entered.set()
        allow_factory_to_finish.wait(timeout=2)
        return Resource()

    resource = LazyResource("test", factory)
    assert resource.initialized is False

    results = []

    def read_value():
        results.append(resource.value)

    first = threading.Thread(target=read_value)
    second = threading.Thread(target=read_value)
    first.start()
    assert factory_entered.wait(timeout=2)
    second.start()
    allow_factory_to_finish.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert results == ["ok", "ok"]
    assert calls == ["build"]
    assert resource.initialized is True


def test_configured_provider_clients_are_not_constructed_at_module_top_level():
    source = CLIENTS_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(CLIENTS_FILE))

    forbidden_calls = {
        "genai.Client",
        "GigaChat",
        "GroqWrapper",
        "ModelFallbackWrapper",
        "GigaChatConversationWrapper",
        "OpenAICompatibleWrapper",
    }
    violations = []

    for node in tree.body:
        for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
            # Calls nested inside factory function bodies are intentionally lazy.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = _call_name(call)
            if name in forbidden_calls:
                violations.append(name)

    assert not violations, (
        "Provider SDK/resource construction returned to import-time: "
        + ", ".join(violations)
    )
