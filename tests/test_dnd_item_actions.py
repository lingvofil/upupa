from types import SimpleNamespace

from AI import dnd_item_actions as item_actions
from AI import dnd_scene_clocks as clocks


def _session():
    return SimpleNamespace(
        participants={"1": {"user_id": 1, "name": "Алиса"}},
        inventories={"1": []},
        conditions={},
        scene_objects={},
        scene_clocks={},
        item_world_facts=[],
        pending_item_uses={},
        item_boosts={},
        scene_count=4,
        threat={"name": None, "level": 0, "max": 6, "history": []},
    )


def _item(name="Медаль за трусость", **extra):
    row = {
        "name": name,
        "kind": "artifact",
        "effect": "работает только при честном признании",
        "mechanic": "ADVANTAGE_MOVE",
        "requirement": "CONFESS_FEAR",
        "cost": "NONE",
        "charges_max": 1,
        "charges_remaining": 1,
        "power": 1,
    }
    row.update(extra)
    return row


def test_item_metadata_attaches_fixed_mechanics_and_charges():
    session = _session()
    session.inventories["1"] = [{"name": "Дверная ручка", "kind": "artifact", "effect": "делает дверь"}]
    text = (
        "[ITEM:ADD;PLAYER:1;NAME:Дверная ручка;KIND:artifact;EFFECT:делает дверь;"
        "MECH:CREATE_EXIT;CHARGES:1;COST:DANGER_PLUS_1]"
    )

    class Campaign:
        import re
        META_RE = re.compile(r"\[(THREAT|NPC|ITEM|REP):([^\]]*)\]", re.I)

        @staticmethod
        def _parse_fields(payload):
            parts = [x.strip() for x in payload.split(";") if x.strip()]
            fields = {}
            for part in parts[1:]:
                key, sep, value = part.partition(":")
                if sep:
                    fields[key.upper()] = value.strip()
            return (parts[0] if parts else ""), fields

    cleaned, notices = item_actions.apply_item_action_metadata(Campaign, session, text, "", [])
    assert cleaned == ""
    assert notices == []
    item = session.inventories["1"][0]
    assert item["mechanic"] == "CREATE_EXIT"
    assert item["charges_max"] == 1
    assert item["charges_remaining"] == 1
    assert item["cost"] == "DANGER_PLUS_1"


def test_confession_requirement_is_checked_before_action_is_accepted():
    session = _session()
    session.inventories["1"] = [_item()]

    plan = item_actions._parse_item_use(session, 1, "использую медаль за трусость и убегаю")
    assert plan is not None
    ok, error, _ = item_actions._prevalidate_plan(session, 1, plan)
    assert ok is False
    assert "признаться" in error

    plan = item_actions._parse_item_use(session, 1, "использую медаль за трусость — мне страшно, я убегаю")
    ok, error, validated = item_actions._prevalidate_plan(session, 1, plan)
    assert ok is True
    assert error is None
    assert validated["name"] == "Медаль за трусость"


def test_depleted_item_cannot_be_used_again():
    session = _session()
    session.inventories["1"] = [_item(charges_remaining=0)]
    plan = item_actions._parse_item_use(session, 1, "использую медаль за трусость — мне страшно")
    ok, error, _ = item_actions._prevalidate_plan(session, 1, plan)
    assert ok is False
    assert "зарядов больше нет" in error


def test_create_exit_is_code_state_and_danger_cost_moves_clock():
    session = _session()
    session.inventories["1"] = [
        _item(
            name="Дверная ручка",
            mechanic="CREATE_EXIT",
            requirement="NONE",
            cost="DANGER_PLUS_1",
        )
    ]
    clocks._set_clock(
        session,
        {
            "ID": "alarm",
            "NAME": "Тревога",
            "KIND": "DANGER",
            "VALUE": "2",
            "MAX": "6",
            "WHEN_FULL": "прибывает стража",
        },
    )
    plan = item_actions._parse_item_use(session, 1, "использую дверную ручку")
    ok, _, validated = item_actions._prevalidate_plan(session, 1, plan)
    assert ok is True
    session.pending_item_uses = {"1": validated}

    notices = item_actions.commit_pending_item_uses(session)

    assert session.inventories["1"][0]["charges_remaining"] == 0
    assert session.scene_clocks["alarm"]["value"] == 3
    assert any(row.get("name") == "выход от «Дверная ручка»" for row in session.scene_objects.values())
    assert any("физический выход" in notice for notice in notices)


def test_item_advantage_cancels_disadvantage_on_matching_domain_only():
    session = _session()
    session.item_boosts = {"1": {"domain": "MOVE", "source": "Медаль"}}

    social = (
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:SOCIAL;REASON:уговорить;"
        "DC:11;MODE:DISADVANTAGE;TARGETS:1]"
    )
    unchanged, source = item_actions.apply_item_boost(session, social)
    assert unchanged == social
    assert source is None
    assert "1" in session.item_boosts

    move = (
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:сбежать;"
        "DC:11;MODE:DISADVANTAGE;TARGETS:1]"
    )
    guarded, source = item_actions.apply_item_boost(session, move)
    assert "MODE:NORMAL" in guarded
    assert source == "Медаль"
    assert "1" not in session.item_boosts


def test_next_accusation_cost_persists_and_deduplicates_until_resolved():
    session = _session()
    session.inventories["1"] = [
        _item(
            name="Справка о невиновности",
            mechanic="CLEAR_ACCUSATION",
            requirement="NONE",
            cost="NEXT_ACCUSATION_SELF",
            charges_max=2,
            charges_remaining=2,
        )
    ]
    plan = item_actions._parse_item_use(session, 1, "использую справку о невиновности")
    ok, _, validated = item_actions._prevalidate_plan(session, 1, plan)
    assert ok
    session.pending_item_uses = {"1": validated}
    item_actions.commit_pending_item_uses(session)
    assert len(session.item_world_facts) == 1
    assert session.item_world_facts[0]["kind"] == "NEXT_ACCUSATION_SELF"

    plan = item_actions._parse_item_use(session, 1, "использую справку о невиновности")
    ok, _, validated = item_actions._prevalidate_plan(session, 1, plan)
    assert ok
    session.pending_item_uses = {"1": validated}
    item_actions.commit_pending_item_uses(session)
    assert len(session.item_world_facts) == 1

    tag = "[ITEMFACT:RESOLVE;PLAYER:1;KIND:NEXT_ACCUSATION_SELF]"
    cleaned, _ = item_actions.apply_item_fact_metadata(session, tag, tag, [])
    assert cleaned == ""
    assert session.item_world_facts == []


def test_new_adventure_restores_active_item_charges():
    session = _session()
    session.inventories["1"] = [_item(charges_max=2, charges_remaining=0)]
    changed = item_actions.reset_adventure_charges(session, 1)
    assert changed is True
    assert session.inventories["1"][0]["charges_remaining"] == 2


def test_inventory_mechanic_text_shows_effect_charge_requirement_and_price():
    item = _item(
        name="Медаль за трусость",
        cost="DANGER_PLUS_1",
        charges_max=2,
        charges_remaining=1,
    )
    text = item_actions.format_item_mechanic(item)
    assert "преимущество на движение/побег" in text
    assert "заряд 1/2" in text
    assert "признаться" in text
    assert "опасность +1" in text
