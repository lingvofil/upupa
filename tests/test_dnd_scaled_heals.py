from types import SimpleNamespace

from AI import dnd_scaled_heals as scaled
from AI import dnd_two_heals as two_heals


def _session(size, charges=None):
    participants = {
        str(user_id): {"user_id": user_id, "name": f"Игрок {user_id}"}
        for user_id in range(1, size + 1)
    }
    sheets = {
        str(user_id): {"hp": 8, "max_hp": 8, "status": "alive"}
        for user_id in range(1, size + 1)
    }
    return SimpleNamespace(
        participants=participants,
        character_sheets=sheets,
        healing_charge={"owner_id": 1 if size else None, "used": not bool(size)},
        healing_charges=list(charges or []),
    )


def test_healing_charge_count_scales_one_per_three_heroes_rounded_up():
    expected = {
        0: 0,
        1: 1,
        2: 1,
        3: 1,
        4: 2,
        5: 2,
        6: 2,
        7: 3,
        8: 3,
        9: 3,
        10: 4,
    }

    assert {size: scaled._healing_charge_count(size) for size in expected} == expected


def test_five_hero_party_gets_two_distinct_heal_owners():
    session = _session(5)

    charges = scaled._ensure_scaled_charges(two_heals, session)

    assert len(charges) == 2
    assert len({charge["owner_id"] for charge in charges}) == 2
    assert session.healing_charge["owner_id"] == charges[0]["owner_id"]


def test_three_hero_party_trims_legacy_two_heal_save_to_one():
    session = _session(
        3,
        charges=[
            {"id": 1, "owner_id": 1, "used": False, "lost": False},
            {"id": 2, "owner_id": 2, "used": False, "lost": False},
        ],
    )

    charges = scaled._ensure_scaled_charges(two_heals, session)

    assert charges == [{"id": 1, "owner_id": 1, "used": False, "lost": False}]


def test_seven_hero_party_expands_existing_two_heal_save_to_three_distinct_owners():
    session = _session(
        7,
        charges=[
            {"id": 1, "owner_id": 1, "used": True, "lost": False},
            {"id": 2, "owner_id": 2, "used": False, "lost": False},
        ],
    )

    charges = scaled._ensure_scaled_charges(two_heals, session)

    assert len(charges) == 3
    assert charges[0]["used"] is True
    assert len({charge["owner_id"] for charge in charges}) == 3
    assert [charge["id"] for charge in charges] == [1, 2, 3]


def test_used_heals_still_count_toward_party_quota():
    session = _session(
        5,
        charges=[
            {"id": 1, "owner_id": 1, "used": True, "lost": False},
            {"id": 2, "owner_id": 2, "used": False, "lost": False},
        ],
    )

    charges = scaled._ensure_scaled_charges(two_heals, session)

    assert len(charges) == 2
    assert sum(not charge["used"] and not charge["lost"] for charge in charges) == 1


def test_decision_copy_has_no_hardcoded_two_heal_language():
    text, prompt = scaled._generalize_decision_copy(
        "Но есть вторая: теперь решает Боря со второй лечилкой.",
        "Алиса потратила одну из двух аварийных лечилок.",
    )

    assert "есть ещё лечилка" in text
    assert "со следующей лечилкой" in text
    assert "одну из аварийных лечилок" in prompt
    assert "двух" not in prompt


def test_scaled_rule_documents_five_player_quota():
    assert "4–6 — 2" in scaled.SCALED_HEAL_RULE
    assert "одна лечилка на каждые три героя" in scaled.SCALED_HEAL_RULE
