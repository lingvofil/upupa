from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_cinematic_combat as cinematic
from AI import dnd_combat as combat
from AI import dnd_player_combat as player_combat


BASE_STATS = {
    "STR": 16,
    "DEX": 14,
    "CON": 13,
    "INT": 12,
    "WIS": 10,
    "CHA": 8,
}


def _session():
    return SimpleNamespace(
        participants={"1": {"user_id": 1, "name": "Алиса"}},
        character_sheets={
            "1": {
                "stats": dict(BASE_STATS),
                "hp": 14,
                "max_hp": 14,
                "ac": 13,
                "status": "alive",
            }
        },
        enemy_combatants={},
    )


def _pending(enemy_key: str, *, dc=16, ability="STR", method="свалить огромный валун"):
    return {
        "type": "CINEMATIC_ATTACK",
        "dc": dc,
        "mode": "NORMAL",
        "target_user_ids": [1],
        "cinematic": {
            "enemy_key": enemy_key,
            "enemy_name": "Огр",
            "ability": ability,
            "method": method,
            "power": "HIGH",
            "requested_dc": dc,
        },
    }


def test_parse_cinematic_attack_supports_lethal_environmental_fields():
    action = cinematic._parse_cinematic_attack(
        "Скала дрожит. "
        "[ACTION:CINEMATIC_ATTACK;TARGETS:1;ENEMY:Огр;POWER:HIGH;HP:28;AC:14;"
        "ABILITY:STR;DC:16;MODE:ADVANTAGE;METHOD:свалить огромный валун со скалы на огра;"
        "REASON:Алиса толкает валун, когда огр проходит внизу]"
    )

    assert action == {
        "target_user_ids": [1],
        "enemy_name": "Огр",
        "power": "HIGH",
        "enemy_hp": "28",
        "enemy_ac": "14",
        "ability": "STR",
        "requested_dc": 16,
        "mode": "ADVANTAGE",
        "method": "свалить огромный валун со скалы на огра",
        "reason": "Алиса толкает валун, когда огр проходит внизу",
    }


def test_cinematic_dc_cannot_be_softened_below_enemy_power_floor():
    action = {"power": "LOW", "requested_dc": 5}

    assert cinematic._cinematic_dc(action, {"power": "LOW"}) == 12
    assert cinematic._cinematic_dc(action, {"power": "MEDIUM"}) == 14
    assert cinematic._cinematic_dc(action, {"power": "HIGH"}) == 16
    assert cinematic._cinematic_dc(action, {"power": "DEADLY"}) == 18


def test_successful_cinematic_action_can_defeat_high_enemy_regardless_of_remaining_hp():
    session = _session()
    key, enemy = player_combat._register_enemy(
        session,
        name="Огр",
        power="HIGH",
        hp=28,
        ac=14,
    )
    enemy["hp"] = 27

    summary, prompt = cinematic._resolve_cinematic_mechanics(
        combat,
        player_combat,
        session,
        1,
        _pending(key, dc=16),
        natural=14,
    )

    # STR 16 gives +3: 14 + 3 = 17 against DC 16.
    assert enemy["hp"] == 0
    assert enemy["status"] == "dead"
    assert "27/28 → 0/28" in summary
    assert "побеждён нестандартным действием" in summary
    assert "специальное механическое исключение" in prompt


def test_failed_cinematic_action_does_no_free_weapon_damage():
    session = _session()
    key, enemy = player_combat._register_enemy(
        session,
        name="Огр",
        power="HIGH",
        hp=28,
        ac=14,
    )
    enemy["hp"] = 19

    summary, prompt = cinematic._resolve_cinematic_mechanics(
        combat,
        player_combat,
        session,
        1,
        _pending(key, dc=18),
        natural=7,
    )

    assert enemy["hp"] == 19
    assert enemy["status"] == "alive"
    assert "HP цели не меняется" in summary
    assert "не наноси ей бесплатный обычный урон" in prompt


def test_natural_twenty_can_land_cinematic_kill_even_when_total_is_below_dc():
    session = _session()
    session.character_sheets["1"]["stats"]["CHA"] = 1
    key, enemy = player_combat._register_enemy(
        session,
        name="Дракон",
        power="DEADLY",
        hp=45,
        ac=16,
    )

    summary, _prompt = cinematic._resolve_cinematic_mechanics(
        combat,
        player_combat,
        session,
        1,
        _pending(key, dc=20, ability="CHA", method="заманить дракона под обвал"),
        natural=20,
    )

    assert enemy["hp"] == 0
    assert enemy["status"] == "dead"
    assert "КРИТИЧЕСКАЯ УДАЧА" in summary


def test_natural_one_always_fails_cinematic_action():
    session = _session()
    session.character_sheets["1"]["stats"]["STR"] = 40
    key, enemy = player_combat._register_enemy(
        session,
        name="Гоблин",
        power="LOW",
        hp=8,
        ac=10,
    )

    summary, _prompt = cinematic._resolve_cinematic_mechanics(
        combat,
        player_combat,
        session,
        1,
        _pending(key, dc=12),
        natural=1,
    )

    assert enemy["hp"] == 8
    assert enemy["status"] == "alive"
    assert "КРИТИЧЕСКАЯ НЕУДАЧА" in summary


def test_rules_make_cinematic_kill_a_narrow_environmental_exception():
    rules = " ".join(cinematic.CINEMATIC_COMBAT_RULES.casefold().split())

    assert "единственное исключение" in rules
    assert "огромный валун" in rules
    assert "нельзя использовать эту механику для обычного удара оружием" in rules
    assert "желание игрока убить с одного раза само по себе не создаёт право на ваншот" in rules
    assert "при провале hp цели не уменьшается вообще" in rules
