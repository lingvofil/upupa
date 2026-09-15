from pathlib import Path
from types import SimpleNamespace

from AI.dnd_inventory_context import DndInventoryContextPolicy, configure_dnd_inventory_context
from AI.dnd_inventory_effects import _inventory_context as render_inventory_effect_context
from AI.dnd_inventory_fun import _inventory_context as render_fun_inventory_context


ROOT = Path(__file__).resolve().parents[1]


def _session():
    return SimpleNamespace(
        inventories={
            "7": [
                {
                    "name": "ложка",
                    "few": "ложки",
                    "many": "ложек",
                    "kind": "item",
                    "quantity": 5,
                    "bonus_per_unit": 1,
                    "trait": "прожорливости",
                }
            ]
        },
        artifact_awards={},
        scene_count=2,
    )


def test_inventory_context_policy_preserves_renderer_upgrade_order():
    campaign = SimpleNamespace(_ensure=lambda _session: None, _inventory_context=lambda _session: "base")
    session = _session()
    policy = DndInventoryContextPolicy(render_fun_inventory_context)

    configured = configure_dnd_inventory_context(campaign, policy)

    assert configured is policy
    fun_text = campaign._inventory_context(session)
    assert "5 ложек" in fun_text
    assert "+5 к прожорливости" not in fun_text

    policy.renderer = render_inventory_effect_context
    effect_text = campaign._inventory_context(session)
    assert "5 ложек — +5 к прожорливости" in effect_text


def test_inventory_context_configuration_is_idempotent():
    campaign = SimpleNamespace(_ensure=lambda _session: None, _inventory_context=lambda _session: "base")
    first = DndInventoryContextPolicy(lambda _campaign, _session: "first")
    second = DndInventoryContextPolicy(lambda _campaign, _session: "second")

    assert configure_dnd_inventory_context(campaign, first) is first
    assert configure_dnd_inventory_context(campaign, second) is first
    assert campaign._inventory_context(_session()) == "first"


def test_inventory_context_is_composed_without_extension_monkeypatches():
    fun_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    effects_source = (ROOT / "AI" / "dnd_inventory_effects.py").read_text(encoding="utf-8")
    policy_source = (ROOT / "AI" / "dnd_inventory_context.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")
    fun_install = "install_fun_inventory(state_policy=campaign_state_policy)"
    effects_install = "    install_dnd_inventory_effects(\n"

    assert "campaign._inventory_context =" not in fun_source
    assert "campaign._inventory_context =" not in effects_source
    assert "campaign._inventory_context =" in policy_source
    assert "DndInventoryContextPolicy(render_fun_inventory_context)" in runtime_source
    assert "configure_dnd_inventory_context(campaign, inventory_context_policy)" in runtime_source
    assert "inventory_context_policy.renderer = render_inventory_effect_context" in runtime_source
    assert "state_policy=campaign_state_policy" in runtime_source
    assert runtime_source.index(fun_install) < runtime_source.index(
        "configure_dnd_inventory_context(campaign, inventory_context_policy)"
    ) < runtime_source.index(effects_install)
    assert runtime_source.index(effects_install) < runtime_source.index(
        "inventory_context_policy.renderer = render_inventory_effect_context"
    )
