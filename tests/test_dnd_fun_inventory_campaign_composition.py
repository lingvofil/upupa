from pathlib import Path
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_campaign
from AI.dnd_inventory_fun import (
    FUN_INVENTORY_RULES,
    build_fun_inventory_campaign_composition,
)


ROOT = Path(__file__).resolve().parents[1]


def _base_ensure(session):
    if not hasattr(session, "participants"):
        session.participants = {}
    if not hasattr(session, "inventories"):
        session.inventories = {}


def _base_state(session):
    _base_ensure(session)
    return {"inventories": session.inventories}


def _base_apply(session, text):
    _base_ensure(session)
    notices = []
    valid_players = {
        str(int(item["user_id"]))
        for item in session.participants.values()
        if item.get("user_id") is not None
    }
    for match in dnd_campaign.META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = dnd_campaign._parse_fields(payload)
        player = fields.get("PLAYER", "")
        item_name = fields.get("NAME")
        if not player.isdigit() or not item_name or (valid_players and player not in valid_players):
            continue
        inventory = session.inventories.setdefault(player, [])
        if head.upper() == "ADD" and not any(
            (item.get("name") if isinstance(item, dict) else str(item)).casefold() == item_name.casefold()
            for item in inventory
        ):
            inventory.append({"name": item_name, "kind": fields.get("KIND", "item").lower()})
            notices.append(f"add:{item_name}")
        elif head.upper() == "REMOVE":
            session.inventories[player] = [
                item
                for item in inventory
                if (item.get("name") if isinstance(item, dict) else str(item)).casefold() != item_name.casefold()
            ]
            notices.append(f"remove:{item_name}")
    return dnd_campaign.META_RE.sub("", str(text or "")).strip(), notices


def _campaign():
    return SimpleNamespace(
        _ensure=_base_ensure,
        _state=_base_state,
        _apply_metadata=_base_apply,
        _inventory_context=lambda _session: "base inventory",
        RULES="BASE RULES",
        META_RE=dnd_campaign.META_RE,
        _parse_fields=dnd_campaign._parse_fields,
    )


def _session():
    return SimpleNamespace(
        participants={"1": {"user_id": 1, "name": "Детектор"}},
        inventories={},
        scene_count=0,
    )


def test_builder_describes_campaign_composition_without_mutating_campaign():
    campaign = _campaign()
    original_ensure = campaign._ensure
    original_state = campaign._state
    original_apply = campaign._apply_metadata
    original_context = campaign._inventory_context

    composition = build_fun_inventory_campaign_composition(campaign)

    assert campaign._ensure is original_ensure
    assert campaign._state is original_state
    assert campaign._apply_metadata is original_apply
    assert campaign._inventory_context is original_context
    assert campaign.RULES == "BASE RULES"
    assert composition.rules == f"BASE RULES\n{FUN_INVENTORY_RULES}"


def test_composed_ensure_and_state_persist_artifact_awards():
    composition = build_fun_inventory_campaign_composition(_campaign())
    session = _session()

    composition.ensure(session)
    assert session.artifact_awards == {}

    session.artifact_awards = {"1": ["Корона Подъезда"]}
    row = composition.state(session)

    assert row["artifact_awards"] == {"1": ["Корона Подъезда"]}


def test_composed_metadata_keeps_stacks_and_records_artifact_awards():
    composition = build_fun_inventory_campaign_composition(_campaign())
    session = _session()
    stack_tag = "[ITEM:ADD;PLAYER:1;NAME:штраф;FEW:штрафа;MANY:штрафов;KIND:item]"

    composition.apply_metadata(session, stack_tag)
    composition.apply_metadata(session, stack_tag)
    composition.apply_metadata(
        session,
        "[ITEM:ADD;PLAYER:1;NAME:Корона Подъезда;KIND:artifact]",
    )

    assert session.inventories["1"][0] == {
        "name": "штраф",
        "kind": "item",
        "quantity": 2,
        "few": "штрафа",
        "many": "штрафов",
    }
    assert session.inventories["1"][1] == {"name": "Корона Подъезда", "kind": "artifact"}
    assert session.artifact_awards == {"1": ["Корона Подъезда"]}


def test_composed_inventory_context_keeps_stack_forms_and_artifact_progress():
    composition = build_fun_inventory_campaign_composition(_campaign())
    session = _session()
    session.inventories = {
        "1": [
            {"name": "штраф", "kind": "item", "quantity": 2, "few": "штрафа", "many": "штрафов"},
            {"name": "Корона Подъезда", "kind": "artifact"},
        ]
    }
    session.artifact_awards = {"1": ["Корона Подъезда"]}

    context = composition.inventory_context(session)

    assert "- ID 1: 2 штрафа, Корона Подъезда" in context
    assert "Новых артефактов этой кампании: 1 (Корона Подъезда)" in context


def test_fun_inventory_campaign_wiring_is_owned_by_runtime():
    inventory_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "def install_fun_inventory" not in inventory_source
    assert "campaign._ensure =" not in inventory_source
    assert "campaign._state =" not in inventory_source
    assert "campaign._apply_metadata =" not in inventory_source
    assert "campaign._inventory_context =" not in inventory_source
    assert "campaign.RULES =" not in inventory_source

    assert "build_fun_inventory_campaign_composition(campaign)" in runtime_source
    assert "campaign._ensure = campaign_composition.ensure" in runtime_source
    assert "campaign._state = campaign_composition.state" in runtime_source
    assert "campaign._apply_metadata = campaign_composition.apply_metadata" in runtime_source
    assert "campaign._inventory_context = campaign_composition.inventory_context" in runtime_source
    assert "campaign.RULES = campaign_composition.rules" in runtime_source
    assert "_upupa_fun_inventory_composition" in runtime_source
