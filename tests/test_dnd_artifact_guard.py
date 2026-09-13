from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_artifact_guard import (
    ARTIFACT_GUARD_RULES,
    apply_artifact_guard,
    strip_artifact_theft,
)
from AI.dnd_inventory_fun import transfer_between_inventories


def _session():
    return SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Владелец"},
            "2": {"user_id": 2, "name": "Вор"},
        },
        inventories={
            "1": [{"name": "Ботинок Истины", "kind": "artifact"}],
            "2": [],
        },
    )


def test_story_metadata_cannot_move_artifact_between_heroes():
    session = _session()
    text = (
        "Вор вырывает Ботинок Истины.\n"
        "[ITEM:REMOVE;PLAYER:1;NAME:Ботинок Истины]\n"
        "[ITEM:ADD;PLAYER:2;NAME:Ботинок Истины;KIND:artifact]\n"
        "[ACTION:INPUT]"
    )

    guarded, blocked = strip_artifact_theft(dnd_campaign, session, text)

    assert "ITEM:REMOVE" not in guarded
    assert "ITEM:ADD" not in guarded
    assert "[ACTION:INPUT]" in guarded
    assert blocked == [("1", "Ботинок Истины")]


def test_guard_adds_clear_notice_and_keeps_owner_inventory():
    session = _session()
    text = (
        "[ITEM:REMOVE;PLAYER:1;NAME:Ботинок Истины]"
        "[ITEM:ADD;PLAYER:2;NAME:Ботинок Истины;KIND:artifact]"
    )

    def fake_apply(_session, guarded_text):
        return guarded_text, []

    cleaned, notices = apply_artifact_guard(dnd_campaign, fake_apply, session, text)

    assert "ITEM:" not in cleaned
    assert notices == ["✨ Ботинок Истины остаётся у Владелец: чужие артефакты нельзя отбирать."]
    assert session.inventories["1"][0]["name"] == "Ботинок Истины"
    assert session.inventories["2"] == []


def test_ordinary_items_are_not_protected_by_artifact_guard():
    session = _session()
    session.inventories["1"].append({"name": "ложка", "kind": "item"})
    text = (
        "[ITEM:REMOVE;PLAYER:1;NAME:ложка]"
        "[ITEM:ADD;PLAYER:2;NAME:ложка;KIND:item]"
    )

    guarded, blocked = strip_artifact_theft(dnd_campaign, session, text)

    assert guarded == text
    assert blocked == []


def test_owner_can_still_voluntarily_transfer_artifact_with_command_mechanic():
    session = _session()

    display, kind = transfer_between_inventories(
        session.inventories,
        sender_id=1,
        target_id=2,
        item_query="Ботинок Истины",
        quantity=1,
    )

    assert display == "Ботинок Истины"
    assert kind == "artifact"
    assert session.inventories["1"] == []
    assert session.inventories["2"] == [{"name": "Ботинок Истины", "kind": "artifact"}]


def test_prompt_distinguishes_theft_from_owner_transfer():
    text = ARTIFACT_GUARD_RULES.casefold()
    assert "не может украсть" in text
    assert "владелец артефакта не меняется" in text
    assert "передать" in text
