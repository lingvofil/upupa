from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_combat as combat
from AI import dnd_healing_choice as healing


def _session(*, owner_id=1):
    return SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        character_sheets={
            "1": {"hp": 10, "max_hp": 10, "ac": 12, "status": "alive", "stats": {}},
            "2": {"hp": 2, "max_hp": 12, "ac": 10, "status": "alive", "stats": {}},
        },
        healing_charge={"owner_id": owner_id, "used": False},
        pending_heal_decision=None,
    )


def test_lethal_attack_waits_for_healer_instead_of_auto_consuming(monkeypatch):
    session = _session()
    values = iter([15, 4])
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: next(values))
    monkeypatch.setattr(healing.random, "randint", lambda _a, _b: 123456)

    summary, _prompt, all_dead, pending = healing._resolve_enemy_attack_with_choice(
        combat,
        session,
        {"target_user_id": 2, "power": "MEDIUM", "reason": "орк рубит Борю"},
    )

    assert pending is True
    assert all_dead is False
    assert session.character_sheets["2"]["hp"] == 0
    assert session.character_sheets["2"]["status"] == "dying"
    assert session.healing_charge["used"] is False
    assert session.pending_heal_decision["target_id"] == 2
    assert session.pending_heal_decision["owner_id"] == 1
    assert session.pending_heal_decision["nonce"] == 123456
    assert session.pending_heal_decision["reason"] == "орк рубит Борю"
    assert "при смерти" in summary
    assert "выбывает из этой егры" not in summary


def test_refusal_kills_target_but_preserves_heal_for_someone_else():
    session = _session()
    session.character_sheets["2"]["hp"] = 0
    session.character_sheets["2"]["status"] = "dying"
    session.pending_heal_decision = {
        "target_id": 2,
        "owner_id": 1,
        "nonce": 111111,
        "attack_prompt": "Боря получил смертельный удар.",
        "reason": "удар",
    }

    text, prompt, all_dead = healing._apply_heal_decision(combat, session, use_heal=False)

    assert session.character_sheets["2"]["status"] == "dead"
    assert session.healing_charge["used"] is False
    assert session.pending_heal_decision is None
    assert all_dead is False
    assert "Лечилка осталась" in text
    assert "НЕ израсходована" in prompt


def test_healer_can_accept_and_charge_is_consumed(monkeypatch):
    session = _session()
    session.character_sheets["2"]["hp"] = 0
    session.character_sheets["2"]["status"] = "dying"
    session.pending_heal_decision = {
        "target_id": 2,
        "owner_id": 1,
        "nonce": 222222,
        "attack_prompt": "Боря получил смертельный удар.",
        "reason": "удар",
    }
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: 4)

    text, prompt, all_dead = healing._apply_heal_decision(combat, session, use_heal=True)

    assert session.character_sheets["2"]["status"] == "alive"
    assert session.character_sheets["2"]["hp"] == 6
    assert session.healing_charge["used"] is True
    assert session.pending_heal_decision is None
    assert all_dead is False
    assert "возвращается с 6 HP" in text
    assert "лечилка израсходована" in prompt


def test_refusing_self_heal_loses_charge_with_dead_owner():
    session = _session(owner_id=1)
    session.character_sheets["1"]["hp"] = 0
    session.character_sheets["1"]["status"] = "dying"
    session.pending_heal_decision = {
        "target_id": 1,
        "owner_id": 1,
        "nonce": 333333,
        "attack_prompt": "Алиса получила смертельный удар.",
        "reason": "удар",
    }

    text, _prompt, all_dead = healing._apply_heal_decision(combat, session, use_heal=False)

    assert session.character_sheets["1"]["status"] == "dead"
    assert session.healing_charge["used"] is True
    assert session.healing_charge["lost"] is True
    assert all_dead is False
    assert "лечилка пропала" in text
