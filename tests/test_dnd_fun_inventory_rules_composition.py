from pathlib import Path
from types import SimpleNamespace

from AI.dnd_inventory_fun import FUN_INVENTORY_RULES, configure_fun_inventory_rules


ROOT = Path(__file__).resolve().parents[1]


def test_fun_inventory_rules_are_added_to_system_prompt_once():
    dnd = SimpleNamespace(DND_SYSTEM_PROMPT="base prompt")

    configure_fun_inventory_rules(dnd)
    configure_fun_inventory_rules(dnd)

    assert dnd.DND_SYSTEM_PROMPT == f"base prompt\n\n{FUN_INVENTORY_RULES}"


def test_fun_inventory_rule_composition_does_not_mutate_campaign_rules():
    inventory_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "campaign.RULES =" not in inventory_source
    assert "configure_fun_inventory_rules(dnd)" in runtime_source
    assert runtime_source.index("configure_dnd_campaign(dnd, router, completion_policy=completion_policy)") < runtime_source.index(
        "configure_fun_inventory_rules(dnd)"
    )
