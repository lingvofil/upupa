from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_inventory_fun import (
    FUN_INVENTORY_RULES,
    apply_stackable_metadata,
    format_inventory_item,
    render_inventory_lines,
)


def _session():
    return SimpleNamespace(
        participants={"1": {"user_id": 1, "name": "Детектор"}},
    )


def _apply(session, text):
    return apply_stackable_metadata(
        dnd_campaign,
        dnd_campaign._apply_metadata,
        session,
        text,
    )


def test_fun_inventory_rules_explain_event_trophies_and_stacking():
    assert "смешные следы реально случившихся событий" in FUN_INVENTORY_RULES
    assert "Одинаковые обычные предметы стакаются" in FUN_INVENTORY_RULES
    assert "NAME:пизд" in FUN_INVENTORY_RULES
    assert "MANY:пиздов" in FUN_INVENTORY_RULES


def test_repeated_funny_item_stacks_to_five_with_russian_form():
    session = _session()
    tag = "[ITEM:ADD;PLAYER:1;NAME:пизд;FEW:пизда;MANY:пиздов;KIND:item]"

    for _ in range(5):
        _apply(session, tag)

    assert session.inventories["1"] == [
        {
            "name": "пизд",
            "kind": "item",
            "quantity": 5,
            "few": "пизда",
            "many": "пиздов",
        }
    ]
    assert format_inventory_item(session.inventories["1"][0]) == "5 пиздов"
    assert render_inventory_lines(session.inventories["1"]) == ["• 5 пиздов"]


def test_russian_stack_forms_handle_one_few_many_and_twenty_one():
    item = {
        "name": "пизд",
        "few": "пизда",
        "many": "пиздов",
        "kind": "item",
    }

    assert format_inventory_item({**item, "quantity": 1}) == "пизд"
    assert format_inventory_item({**item, "quantity": 2}) == "2 пизда"
    assert format_inventory_item({**item, "quantity": 5}) == "5 пиздов"
    assert format_inventory_item({**item, "quantity": 21}) == "21 пизд"


def test_artifacts_remain_unique_instead_of_stacking():
    session = _session()
    tag = "[ITEM:ADD;PLAYER:1;NAME:Ботинок Истины;KIND:artifact]"

    _apply(session, tag)
    _apply(session, tag)

    assert session.inventories["1"] == [
        {"name": "Ботинок Истины", "kind": "artifact"}
    ]


def test_removing_one_unit_from_stack_keeps_the_rest():
    session = _session()
    add = "[ITEM:ADD;PLAYER:1;NAME:штраф;FEW:штрафа;MANY:штрафов;KIND:item]"
    remove = "[ITEM:REMOVE;PLAYER:1;NAME:штраф]"

    for _ in range(3):
        _apply(session, add)
    _apply(session, remove)

    assert format_inventory_item(session.inventories["1"][0]) == "2 штрафа"
