from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_inventory_descriptions import (
    INVENTORY_DESCRIPTION_RULES,
    apply_missing_item_descriptions,
    format_inventory_entry,
    render_inventory_lines,
)


def _session(items=None):
    return SimpleNamespace(
        mode="participants",
        participants={"1": {"user_id": 1, "name": "Каменщик"}},
        inventories={"1": list(items or [])},
    )


def test_plain_item_always_has_visible_characteristic_without_mutation():
    item = {"name": "камень", "kind": "item", "quantity": 1}
    original = dict(item)

    rendered = format_inventory_entry(item)

    assert rendered.startswith("камень — ")
    assert len(rendered) > len("камень — ")
    assert render_inventory_lines([item])[0].startswith("• камень — ")
    assert item == original


def test_explicit_effect_is_preserved_verbatim():
    item = {
        "name": "Компас",
        "kind": "item",
        "quantity": 1,
        "effect": "стрелка дёргается рядом с тайными проходами",
    }

    assert format_inventory_entry(item) == (
        "Компас — стрелка дёргается рядом с тайными проходами"
    )


def test_missing_item_add_effect_gets_fallback_notice_without_storage_mutation():
    stored = {"name": "камень", "kind": "item", "quantity": 1}
    session = _session([stored])
    text = "[ITEM:ADD;PLAYER:1;NAME:камень;KIND:item]"
    notices = ["🎒 Каменщик получает: камень"]

    cleaned, updated = apply_missing_item_descriptions(
        dnd_campaign,
        session,
        text,
        "",
        notices,
    )

    assert cleaned == ""
    assert session.inventories["1"][0] == stored
    assert session.inventories["1"][0] is stored
    assert "effect" not in stored and "bonus_per_unit" not in stored
    assert updated[0].startswith("🎒 Каменщик получает: камень — ")


def test_description_prompt_requires_characteristic_for_every_new_item():
    rules = INVENTORY_DESCRIPTION_RULES.casefold()

    assert "каждый новый item:add" in rules
    assert "не оставляй новый предмет только с name/kind" in rules
    assert "обязательно" in rules
    assert "effect не обязан быть механическим бонусом" in rules
