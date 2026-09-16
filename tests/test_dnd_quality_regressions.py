from types import SimpleNamespace

from AI.dnd_manual_healing import _apply_manual_heal, strip_healing_poll_options
from AI.dnd_pacing import guard_enemy_attack_chain
from AI.dnd_profile_ownership import profile_owner_id


def test_profile_button_owner_is_encoded_and_parseable():
    assert profile_owner_id("dnd:prof:12345:pick:style:2") == 12345
    assert profile_owner_id("dnd:plot:2") is None


def test_healing_poll_option_is_removed():
    response = (
        "Сцена. "
        "[ACTION:POLL;OPTIONS:Лезть в вентиляцию;Вылечить Детектора;Бежать наружу]"
    )
    cleaned = strip_healing_poll_options(response)
    assert "Вылечить Детектора" not in cleaned
    assert "Лезть в вентиляцию;Бежать наружу" in cleaned


def test_poll_with_only_one_non_heal_option_becomes_input():
    response = "[ACTION:POLL;TARGETS:123;OPTIONS:Лечить Алису;Ждать]"
    assert strip_healing_poll_options(response) == "[ACTION:INPUT;TARGETS:123]"


def test_second_enemy_attack_is_returned_to_players():
    first, flag, blocked = guard_enemy_attack_chain(
        "Паук бросается. [ACTION:ENEMY_ATTACK;TARGETS:1;POWER:LOW]",
        False,
    )
    assert "ENEMY_ATTACK" in first
    assert flag is True
    assert blocked is False

    second, flag, blocked = guard_enemy_attack_chain(
        "Второй паук тоже лезет. [ACTION:ENEMY_ATTACK;TARGETS:2;POWER:LOW]",
        flag,
    )
    assert "ENEMY_ATTACK" not in second
    assert "[ACTION:INPUT]" in second
    assert flag is False
    assert blocked is True


def test_manual_heal_consumes_owner_charge_and_caps_hp():
    session = SimpleNamespace(
        healing_charges=[{"id": 1, "owner_id": 10, "used": False, "lost": False}],
        healing_charge={"owner_id": 10, "used": False},
        character_sheets={
            "10": {"hp": 14, "max_hp": 14, "status": "alive"},
            "20": {"hp": 11, "max_hp": 14, "status": "alive"},
        },
        participants={
            "10": {"user_id": 10, "name": "Лекарь"},
            "20": {"user_id": 20, "name": "Детектор"},
        },
    )

    class FakeTwoHeals:
        @staticmethod
        def _charge_by_id(current, charge_id):
            return current.healing_charges[0] if charge_id == 1 else None

        @staticmethod
        def _owner_can_use(current, owner_id, _target):
            return owner_id == 10

        @staticmethod
        def _sync_legacy_charge(current):
            current.healing_charge["used"] = current.healing_charges[0]["used"]

    class FakeCombat:
        @staticmethod
        def _participant_name(current, user_id):
            return current.participants[str(user_id)]["name"]

    ok, text = _apply_manual_heal(
        FakeTwoHeals,
        FakeCombat,
        session,
        owner_id=10,
        charge_id=1,
        target_id=20,
        roll=10,
    )
    assert ok is True
    assert session.character_sheets["20"]["hp"] == 14
    assert session.healing_charges[0]["used"] is True
    assert "+3 HP" in text
