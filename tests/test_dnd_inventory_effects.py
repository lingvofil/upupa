from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_inventory_effects import (
    INVENTORY_EFFECT_RULES,
    apply_item_effect_metadata,
    format_inventory_effect,
    format_inventory_entry,
    render_inventory_lines,
)
from AI.dnd_inventory_fun import apply_stackable_metadata, transfer_between_inventories
from AI.dnd_inventory_reliability import _expand_quantity_tags


def _session():
    return SimpleNamespace(
        mode="participants",
        participants={
            "1": {"user_id": 1, "name": "Ложечник"},
            "2": {"user_id": 2, "name": "Получатель"},
        },
    )


def _apply(session, text):
    def stacked_apply(current_session, raw_text):
        return apply_stackable_metadata(
            dnd_campaign,
            dnd_campaign._apply_metadata,
            current_session,
            _expand_quantity_tags(raw_text),
        )

    return apply_item_effect_metadata(dnd_campaign, stacked_apply, session, text)


def test_stack_bonus_scales_with_quantity():
    session = _session()
    tag = (
        "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:5;FEW:ложки;MANY:ложек;KIND:item;"
        "BONUS:1;TRAIT:прожорливости]"
    )

    _apply(session, tag)

    item = session.inventories["1"][0]
    assert item["quantity"] == 5
    assert item["bonus_per_unit"] == 1
    assert item["trait"] == "прожорливости"
    assert format_inventory_effect(item) == "+5 к прожорливости"
    assert format_inventory_entry(item) == "5 ложек — +5 к прожорливости"
    assert render_inventory_lines([item]) == ["• 5 ложек — +5 к прожорливости"]


def test_freeform_effect_is_shown_for_artifact():
    session = _session()
    tag = (
        "[ITEM:ADD;PLAYER:1;NAME:Перстень мокрого барона;KIND:artifact;"
        "EFFECT:звенит рядом с болотной нечистью]"
    )

    _apply(session, tag)

    item = session.inventories["1"][0]
    assert item["effect"] == "звенит рядом с болотной нечистью"
    assert format_inventory_entry(item) == (
        "Перстень мокрого барона — звенит рядом с болотной нечистью"
    )
    assert render_inventory_lines([item]) == [
        "✨ Перстень мокрого барона — звенит рядом с болотной нечистью"
    ]


def test_partial_transfer_keeps_effect_and_rescales_stack_bonus():
    inventories = {
        "1": [
            {
                "name": "ложка",
                "few": "ложки",
                "many": "ложек",
                "kind": "item",
                "quantity": 5,
                "bonus_per_unit": 1,
                "trait": "прожорливости",
            }
        ],
        "2": [],
    }

    transfer_between_inventories(inventories, 1, 2, "ложка", 2)

    assert format_inventory_entry(inventories["1"][0]) == "3 ложки — +3 к прожорливости"
    assert format_inventory_entry(inventories["2"][0]) == "2 ложки — +2 к прожорливости"


def test_effect_rules_keep_flavor_bonus_out_of_direct_d20_math():
    text = INVENTORY_EFFECT_RULES.casefold()

    assert "bonus" in text
    assert "trait" in text
    assert "effect" in text
    assert "за одну" in text
    assert "не прямые модификаторы" in text
    assert "не число, которое код автоматически прибавляет к d20" in text
    assert "преимущество/помеху" in text
