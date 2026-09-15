from pathlib import Path
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI.dnd_inventory_fun import build_fun_inventory_state_view_policy
from AI.dnd_state_commands import (
    DndStateCommandMiddleware,
    DndStateViewPolicy,
    configure_dnd_state_commands,
)


ROOT = Path(__file__).resolve().parents[1]


def _items():
    return [
        {"name": "штраф", "few": "штрафа", "many": "штрафов", "kind": "item", "quantity": 2},
        {"name": "Корона Подъезда", "kind": "artifact"},
    ]


def test_fun_inventory_view_policy_preserves_stack_formatting_and_transfer_help():
    policy = build_fun_inventory_state_view_policy()

    formatted = policy.render_inventory_items(_items())
    inventory = policy.decorate_inventory("🎒 Инвентарь")

    assert "• 2 штрафа" in formatted
    assert "✨ Корона Подъезда" in formatted
    assert inventory.startswith("🎒 Инвентарь\n\n")
    assert "↪️ Передача:" in inventory


def test_state_view_policy_uses_local_explicit_dependencies():
    policy = DndStateViewPolicy(
        inventory_items_renderer=lambda items: [f"items:{len(items)}"],
    )

    assert policy.render_inventory_items(_items()) == ["items:2"]
    assert policy.decorate_inventory("base") == "base"

    footer_policy = DndStateViewPolicy(
        inventory_items_renderer=lambda _items: [],
        inventory_footer="tail",
    )
    assert footer_policy.decorate_inventory("base") == "base\n\ntail"


def test_state_command_registration_keeps_explicit_view_policy():
    registered = []
    router = SimpleNamespace(
        message=SimpleNamespace(outer_middleware=lambda middleware: registered.append(middleware))
    )
    policy = build_fun_inventory_state_view_policy()

    configure_dnd_state_commands(router, view_policy=policy)
    configure_dnd_state_commands(router, view_policy=object())

    assert len(registered) == 1
    assert isinstance(registered[0], DndStateCommandMiddleware)
    assert registered[0].view_policy is policy
    assert router._upupa_dnd_state_view_policy is policy


def test_inventory_state_view_is_composed_without_state_command_monkeypatches():
    inventory_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "state_commands._inventory_items =" not in inventory_source
    assert "state_commands.render_inventory =" not in inventory_source
    assert "original_render_inventory" not in inventory_source
    assert "build_fun_inventory_state_view_policy()" in runtime_source
    assert "configure_dnd_state_commands(router, view_policy=state_view_policy)" in runtime_source
    assert runtime_source.index("configure_dnd_inventory_transfer(router)") < runtime_source.index(
        "configure_dnd_state_commands(router, view_policy=state_view_policy)"
    ) < runtime_source.index("completion.configure_dnd_completion(router, policy=completion_policy)")
