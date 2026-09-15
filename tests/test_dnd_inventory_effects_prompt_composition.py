from pathlib import Path
from types import SimpleNamespace

from AI.dnd_inventory_effects import (
    INVENTORY_EFFECTS_MARKER,
    INVENTORY_EFFECT_RULES,
    configure_inventory_effect_rules,
)


ROOT = Path(__file__).resolve().parents[1]


def test_inventory_effect_rules_are_composed_explicitly_and_idempotently():
    campaign = SimpleNamespace(RULES="CAMPAIGN BASE")
    dnd = SimpleNamespace(DND_SYSTEM_PROMPT="DND BASE")

    configure_inventory_effect_rules(dnd, campaign=campaign)
    configure_inventory_effect_rules(dnd, campaign=campaign)

    assert campaign.RULES == f"CAMPAIGN BASE\n{INVENTORY_EFFECT_RULES}"
    assert dnd.DND_SYSTEM_PROMPT == f"DND BASE\n\n{INVENTORY_EFFECT_RULES}"
    assert campaign.RULES.count(INVENTORY_EFFECTS_MARKER) == 1
    assert dnd.DND_SYSTEM_PROMPT.count(INVENTORY_EFFECTS_MARKER) == 1


def test_inventory_effect_installer_no_longer_mutates_prompt_rules():
    effects_source = (ROOT / "AI" / "dnd_inventory_effects.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")
    install_source = effects_source.split("def install_dnd_inventory_effects", 1)[1]
    effects_install = "    install_dnd_inventory_effects(\n"
    prompt_compose = "    configure_inventory_effect_rules(dnd)\n"

    assert "def configure_inventory_effect_rules" in effects_source
    assert "campaign.RULES =" not in install_source
    assert "dnd.DND_SYSTEM_PROMPT =" not in install_source
    assert "configure_inventory_effect_rules," in runtime_source
    assert prompt_compose in runtime_source
    assert runtime_source.index(effects_install) < runtime_source.index(prompt_compose)
