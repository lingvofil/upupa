from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

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


def _pending(enemy_key: str, *, weapon="длинный меч", style="MELEE"):
    return {
        "type": "ATTACK",
        "mode": "NORMAL",
        "target_user_ids": [1],
        "attack": {
            "enemy_key": enemy_key,
            "enemy_name": "Огр",
            "weapon": weapon,
            "style": style,
        },
    }


def test_player_attack_tag_parses_combat_fields():
    attack = player_combat._parse_player_attack(
        "Сейчас удар. [ACTION:PLAYER_ATTACK;TARGETS:1;ENEMY:Огр;POWER:HIGH;HP:28;AC:14;"
        "WEAPON:длинный меч;STYLE:MELEE;MODE:ADVANTAGE;REASON:Алиса рубит огра]"
    )

    assert attack == {
        "target_user_ids": [1],
        "enemy_name": "Огр",
        "power": "HIGH",
        "enemy_hp": "28",
        "enemy_ac": "14",
        "weapon": "длинный меч",
        "style": "MELEE",
        "mode": "ADVANTAGE",
        "reason": "Алиса рубит огра",
    }


def test_weapon_damage_table_matches_dnd_like_scale():
    assert player_combat._damage_notation(player_combat._weapon_profile("кулак")) == "1"
    assert player_combat._damage_notation(player_combat._weapon_profile("кинжал")) == "1d4"
    assert player_combat._damage_notation(player_combat._weapon_profile("копьё")) == "1d6"
    assert player_combat._damage_notation(player_combat._weapon_profile("длинный меч")) == "1d8"
    assert player_combat._damage_notation(player_combat._weapon_profile("тяжёлый арбалет")) == "1d10"
    assert player_combat._damage_notation(player_combat._weapon_profile("двуручный меч")) == "2d6"
    assert player_combat._damage_notation(player_combat._weapon_profile("секира")) == "1d12"

    improvised = player_combat._weapon_profile("табуретка")
    assert player_combat._damage_notation(improvised) == "1d4"
    assert improvised["proficiency"] == 0
    assert improvised["improvised"] is True


def test_finesse_uses_better_of_strength_and_dexterity():
    profile = player_combat._weapon_profile("рапира")
    assert player_combat._attack_ability(profile, {"STR": 8, "DEX": 16}) == "DEX"
    assert player_combat._attack_ability(profile, {"STR": 16, "DEX": 14}) == "STR"


def test_strong_enemy_cannot_be_created_as_one_hp_paper_boss():
    session = _session()

    _key, high = player_combat._register_enemy(
        session,
        name="Огр",
        power="HIGH",
        hp=1,
        ac=14,
    )
    _key, deadly = player_combat._register_enemy(
        session,
        name="Дракон",
        power="DEADLY",
        hp=2,
        ac=16,
    )

    assert high["max_hp"] == 28
    assert high["hp"] == 28
    assert deadly["max_hp"] == 45
    assert deadly["hp"] == 45


def test_enemy_hp_is_persistent_and_model_cannot_reset_it():
    session = _session()
    key, enemy = player_combat._register_enemy(session, name="Огр", power="HIGH", hp=28, ac=14)
    enemy["hp"] = 17

    same_key, same_enemy = player_combat._register_enemy(
        session,
        name="Огр",
        power="HIGH",
        hp=999,
        ac=5,
    )

    assert same_key == key
    assert same_enemy["hp"] == 17
    assert same_enemy["max_hp"] == 28
    assert same_enemy["ac"] == 14


def test_normal_longsword_hit_subtracts_deterministic_damage(monkeypatch):
    session = _session()
    key, enemy = player_combat._register_enemy(session, name="Огр", power="HIGH", hp=28, ac=14)
    monkeypatch.setattr(player_combat.random, "randint", lambda _a, _b: 6)

    summary, prompt = player_combat._resolve_attack_mechanics(
        combat,
        session,
        1,
        _pending(key),
        natural=14,
    )

    # Attack: 14 + STR 3 + proficiency 2 = 19. Damage: 1d8 (6) + STR 3 = 9.
    assert enemy["hp"] == 19
    assert enemy["status"] == "alive"
    assert "= 19 против КБ 14 — попадание" in summary
    assert "Урон: 1d8 (6) +3 = 9" in summary
    assert "19/28" in summary
    assert "ЖИВ" in prompt


def test_natural_one_always_misses_player_attack(monkeypatch):
    session = _session()
    key, enemy = player_combat._register_enemy(session, name="Гоблин", power="LOW", hp=8, ac=8)
    monkeypatch.setattr(player_combat.random, "randint", lambda _a, _b: 8)

    summary, _prompt = player_combat._resolve_attack_mechanics(
        combat,
        session,
        1,
        _pending(key),
        natural=1,
    )

    assert enemy["hp"] == 8
    assert "промах" in summary
    assert "НАТУРАЛЬНАЯ 1" in summary


def test_critical_doubles_weapon_dice_but_still_cannot_one_shot_high_enemy(monkeypatch):
    session = _session()
    key, enemy = player_combat._register_enemy(session, name="Огр", power="HIGH", hp=28, ac=14)
    values = iter([6, 6, 6, 6])
    monkeypatch.setattr(player_combat.random, "randint", lambda _a, _b: next(values))

    summary, prompt = player_combat._resolve_attack_mechanics(
        combat,
        session,
        1,
        _pending(key, weapon="двуручный меч"),
        natural=20,
    )

    # Greatsword crit: 4d6 + STR 3 = 27; calibrated HIGH enemy starts at 28 HP.
    assert enemy["hp"] == 1
    assert enemy["status"] == "alive"
    assert "Урон: 4d6 (6+6+6+6) +3 = 27" in summary
    assert "1/28" in summary
    assert "ЖИВ" in prompt


def test_enemy_dies_only_when_hp_reaches_zero(monkeypatch):
    session = _session()
    key, enemy = player_combat._register_enemy(session, name="Гоблин", power="LOW", hp=8, ac=10)
    enemy["hp"] = 4
    monkeypatch.setattr(player_combat.random, "randint", lambda _a, _b: 4)

    summary, prompt = player_combat._resolve_attack_mechanics(
        combat,
        session,
        1,
        _pending(key, weapon="кинжал"),
        natural=15,
    )

    assert enemy["hp"] == 0
    assert enemy["status"] == "dead"
    assert "падает до 0 HP" in summary
    assert "побеждён" in prompt


def test_dead_enemy_cannot_attack_hero():
    session = _session()
    key, enemy = player_combat._register_enemy(session, name="Огр", power="HIGH", hp=28, ac=14)
    enemy["hp"] = 0
    enemy["status"] = "dead"
    downstream_called = False

    def downstream(_session, _attack):
        nonlocal downstream_called
        downstream_called = True
        return "bad", "bad", False

    result = player_combat._sync_enemy_attack(
        session,
        {
            "enemy_name": "Огр",
            "enemy_hp": 28,
            "enemy_ac": 14,
            "power": "HIGH",
        },
        downstream,
    )

    assert downstream_called is False
    assert "атаковать не может" in result[0]
    assert key in session.enemy_combatants


def test_combat_prompt_forbids_narrative_one_shots():
    rules = player_combat.PLAYER_COMBAT_RULES.casefold()

    assert "action:player_attack" in rules
    assert "натуральная 20" in rules
    assert "смерть/победа допустима только когда механика реально довела hp до 0" in rules
    assert "импровизированным оружием 1d4" in rules
