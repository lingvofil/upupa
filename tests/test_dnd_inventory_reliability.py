from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_inventory_fun import apply_stackable_metadata, format_inventory_item
from AI.dnd_inventory_reliability import (
    INVENTORY_RELIABILITY_RULES,
    _expand_quantity_tags,
    _insert_tags_before_action,
    _should_audit,
    _validated_item_tags,
)


def _session():
    return SimpleNamespace(
        mode="participants",
        participants={"1": {"user_id": 1, "name": "Ложечник"}},
    )


def _apply_with_qty(session, text):
    expanded = _expand_quantity_tags(text)
    return apply_stackable_metadata(
        dnd_campaign,
        dnd_campaign._apply_metadata,
        session,
        expanded,
    )


def test_rules_make_confirmed_mundane_loot_mandatory():
    text = INVENTORY_RELIABILITY_RULES.casefold()

    assert "обязательно" in text
    assert "ложкам" in text
    assert "систематически тырит ложки" in text
    assert "qty" in text
    assert "kind:artifact" in text


def test_qty_add_stacks_multiple_spoons_in_one_scene():
    session = _session()
    tag = "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:3;FEW:ложки;MANY:ложек;KIND:item]"

    _apply_with_qty(session, tag)

    assert len(session.inventories["1"]) == 1
    assert session.inventories["1"][0]["quantity"] == 3
    assert format_inventory_item(session.inventories["1"][0]) == "3 ложки"


def test_qty_remove_removes_only_requested_units_from_stack():
    session = _session()
    add = "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:5;FEW:ложки;MANY:ложек;KIND:item]"
    remove = "[ITEM:REMOVE;PLAYER:1;NAME:ложка;QTY:2]"

    _apply_with_qty(session, add)
    _apply_with_qty(session, remove)

    assert format_inventory_item(session.inventories["1"][0]) == "3 ложки"


def test_artifact_qty_is_forced_to_one():
    session = _session()
    tag = "[ITEM:ADD;PLAYER:1;NAME:Ложка Судьбы;QTY:7;KIND:artifact]"

    _apply_with_qty(session, tag)

    assert session.inventories["1"] == [
        {"name": "Ложка Судьбы", "kind": "artifact"}
    ]


def test_audit_triggers_for_spoon_theft_without_item_tag():
    session = _session()
    response = "Ты ловко стырил серебряную ложку и спрятал её в карман.\n[ACTION:INPUT]"

    assert _should_audit(session, "Тырю ложку со стола", response) is True


def test_audit_skips_when_primary_response_already_has_item_tag():
    session = _session()
    response = (
        "Ты стырил ложку.\n"
        "[ITEM:ADD;PLAYER:1;NAME:ложка;KIND:item]\n"
        "[ACTION:INPUT]"
    )

    assert _should_audit(session, "Тырю ложку", response) is False


def test_audit_validation_rejects_unknown_players_and_keeps_qty():
    session = _session()
    audit = (
        "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:3;FEW:ложки;MANY:ложек;KIND:item]\n"
        "[ITEM:ADD;PLAYER:999;NAME:чужая ложка;KIND:item]"
    )

    tags = _validated_item_tags(dnd_campaign, session, audit)

    assert tags == [
        "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:3;FEW:ложки;MANY:ложек;KIND:item]"
    ]


def test_repaired_item_tags_are_inserted_before_action_tag():
    response = "Ложка уже в кармане.\n[ACTION:INPUT]"
    tag = "[ITEM:ADD;PLAYER:1;NAME:ложка;KIND:item]"

    repaired = _insert_tags_before_action(dnd_campaign, response, [tag])

    assert repaired.index(tag) < repaired.index("[ACTION:INPUT]")
    assert repaired.startswith("Ложка уже в кармане.")
