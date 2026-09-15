from pathlib import Path

from AI.dnd_inventory_effects import render_inventory_lines
from AI.dnd_state_commands import DndStateViewPolicy


ROOT = Path(__file__).resolve().parents[1]


def test_inventory_effect_renderer_works_as_explicit_state_view_dependency():
    policy = DndStateViewPolicy(inventory_items_renderer=render_inventory_lines)
    items = [
        {
            "name": "ложка",
            "few": "ложки",
            "many": "ложек",
            "kind": "item",
            "quantity": 5,
            "bonus_per_unit": 1,
            "trait": "прожорливости",
        },
        {
            "name": "Перстень мокрого барона",
            "kind": "artifact",
            "effect": "звенит рядом с болотной нечистью",
        },
    ]

    assert policy.render_inventory_items(items) == [
        "• 5 ложек — +5 к прожорливости",
        "✨ Перстень мокрого барона — звенит рядом с болотной нечистью",
    ]


def test_inventory_effects_do_not_mutate_state_command_renderer():
    effects_source = (ROOT / "AI" / "dnd_inventory_effects.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "state_commands._inventory_items =" not in effects_source
    assert "from AI import dnd_state_commands as state_commands" not in effects_source
    assert "state_view_policy.inventory_items_renderer = render_inventory_effect_lines" in runtime_source
    assert runtime_source.index("install_dnd_inventory_effects(dnd)") < runtime_source.index(
        "state_view_policy.inventory_items_renderer = render_inventory_effect_lines"
    )
