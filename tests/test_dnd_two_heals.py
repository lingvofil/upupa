from types import SimpleNamespace

from AI import dnd_combat as combat
from AI.dnd_two_heals import (
    _apply_heal_decision,
    _available_count,
    _second_owner,
)


def _session():
    return SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Первый"},
            "2": {"user_id": 2, "name": "Второй"},
            "3": {"user_id": 3, "name": "Цель"},
        },
        character_sheets={
            "1": {"hp": 8, "max_hp": 8, "status": "alive"},
            "2": {"hp": 8, "max_hp": 8, "status": "alive"},
            "3": {"hp": 0, "max_hp": 10, "status": "dying"},
        },
        healing_charge={"owner_id": 1, "used": False},
        healing_charges=[
            {"id": 1, "owner_id": 1, "used": False, "lost": False},
            {"id": 2, "owner_id": 2, "used": False, "lost": False},
        ],
        pending_heal_decision={
            "target_id": 3,
            "owner_id": 1,
            "charge_id": 1,
            "declined_charge_ids": [],
            "nonce": 111111,
            "attack_prompt": "Враг смертельно ранил Цель.",
        },
    )


def test_second_heal_owner_is_distinct_when_possible():
    assert _second_owner([1, 2, 3], 1) in {2, 3}
    assert _second_owner([1], 1) == 1


def test_first_refusal_passes_decision_to_second_healer():
    session = _session()

    text, _prompt, all_dead = _apply_heal_decision(combat, session, use_heal=False)

    assert all_dead is False
    assert "Теперь решает Второй" in text
    assert session.character_sheets["3"]["status"] == "dying"
    assert session.pending_heal_decision["owner_id"] == 2
    assert session.pending_heal_decision["charge_id"] == 2
    assert session.healing_charges[0]["used"] is False
    assert session.healing_charges[1]["used"] is False
    assert _available_count(session, target_id=3) == 2


def test_second_healer_can_save_target_after_first_refusal(monkeypatch):
    session = _session()
    _apply_heal_decision(combat, session, use_heal=False)
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: 4)

    text, prompt, all_dead = _apply_heal_decision(combat, session, use_heal=True)

    assert all_dead is False
    assert "Второй тратит" in text
    assert session.character_sheets["3"]["status"] == "alive"
    assert session.character_sheets["3"]["hp"] == 6
    assert session.healing_charges[0]["used"] is False
    assert session.healing_charges[1]["used"] is True
    assert session.pending_heal_decision is None
    assert "одну из двух" in prompt


def test_refusing_self_loses_only_that_charge_and_allows_second():
    session = _session()
    session.character_sheets["1"]["hp"] = 0
    session.character_sheets["1"]["status"] = "dying"
    session.character_sheets["3"]["hp"] = 10
    session.character_sheets["3"]["status"] = "alive"
    session.pending_heal_decision.update({"target_id": 1, "owner_id": 1, "charge_id": 1})

    text, _prompt, all_dead = _apply_heal_decision(combat, session, use_heal=False)

    assert all_dead is False
    assert "она потеряна" in text
    assert session.healing_charges[0]["used"] is True
    assert session.healing_charges[0]["lost"] is True
    assert session.healing_charges[1]["used"] is False
    assert session.pending_heal_decision["owner_id"] == 2
    assert session.character_sheets["1"]["status"] == "dying"
