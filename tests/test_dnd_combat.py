from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_combat as combat


BASE_STATS = {
    "STR": 16,
    "DEX": 14,
    "CON": 13,
    "INT": 12,
    "WIS": 10,
    "CHA": 8,
}


def _session(*, hp=14, max_hp=14, ac=13, heal_owner=2, heal_used=False):
    return SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        character_sheets={
            "1": {
                "stats": dict(BASE_STATS),
                "hp": hp,
                "max_hp": max_hp,
                "ac": ac,
                "status": "alive",
            },
            "2": {
                "stats": dict(BASE_STATS),
                "hp": max_hp,
                "max_hp": max_hp,
                "ac": ac,
                "status": "alive",
            },
        },
        healing_charge={"owner_id": heal_owner, "used": heal_used},
    )


def test_ability_modifier_and_sheet_derive_hp_and_ac():
    assert combat.ability_modifier(16) == 3
    assert combat.ability_modifier(14) == 2
    assert combat.ability_modifier(13) == 1
    assert combat.ability_modifier(10) == 0
    assert combat.ability_modifier(8) == -1

    sheet = combat._build_sheet(BASE_STATS)

    assert sheet["max_hp"] == 14
    assert sheet["hp"] == 14
    assert sheet["ac"] == 13
    assert sheet["status"] == "alive"


def test_stats_payload_accepts_only_standard_array_per_hero():
    raw = (
        '{"1":{"STR":16,"DEX":14,"CON":13,"INT":12,"WIS":10,"CHA":8},'
        '"2":{"STR":16,"DEX":16,"CON":13,"INT":12,"WIS":10,"CHA":8}}'
    )

    parsed = combat._parse_stats_payload(raw, [1, 2])

    assert parsed == {"1": BASE_STATS}


def test_roll_ability_uses_explicit_tag_skill_mapping_and_save_fallback():
    assert combat._extract_roll_ability(
        "текст [ACTION:ROLL;TYPE:CHECK;SKILL:Акробатика;ABILITY:DEX;DC:11;MODE:NORMAL]"
    ) == "DEX"
    assert combat._ability_for_roll(
        {"type": "CHECK", "skill": "Атлетика", "reason": "сломать дверь"}
    ) == "STR"
    assert combat._ability_for_roll(
        {"type": "SAVE", "skill": None, "reason": "успеть увернуться от валуна"}
    ) == "DEX"
    assert combat._ability_for_roll(
        {"type": "SAVE", "skill": None, "reason": "выдержать действие яда"}
    ) == "CON"


def test_enemy_attack_misses_against_armor_class(monkeypatch):
    session = _session(hp=14, ac=13)
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: 7)

    summary, prompt, all_dead = combat._resolve_enemy_attack(
        session,
        {"target_user_id": 1, "power": "MEDIUM", "reason": "гоблин машет ножом"},
    )

    assert "7 + 2 = 9 против КБ 13 — мимо" in summary
    assert session.character_sheets["1"]["hp"] == 14
    assert "промах" in prompt
    assert all_dead is False


def test_first_lethal_hit_consumes_party_heal_and_revives_target(monkeypatch):
    session = _session(hp=2, max_hp=14, ac=13, heal_owner=2, heal_used=False)
    values = iter([15, 6, 5])
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: next(values))

    summary, prompt, all_dead = combat._resolve_enemy_attack(
        session,
        {"target_user_id": 1, "power": "HIGH", "reason": "орк рубит тесаком"},
    )

    assert session.healing_charge["used"] is True
    assert session.character_sheets["1"]["hp"] == 7
    assert session.character_sheets["1"]["status"] == "alive"
    assert "одноразовой лечилкой" in summary
    assert "автоматически сработала" in prompt
    assert all_dead is False


def test_lethal_hit_after_heal_is_used_kills_and_excludes_hero(monkeypatch):
    session = _session(hp=2, max_hp=14, ac=13, heal_owner=2, heal_used=True)
    values = iter([15, 6])
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: next(values))

    summary, prompt, all_dead = combat._resolve_enemy_attack(
        session,
        {"target_user_id": 1, "power": "HIGH", "reason": "орк рубит тесаком"},
    )

    assert session.character_sheets["1"]["hp"] == 0
    assert session.character_sheets["1"]["status"] == "dead"
    assert combat._living_ids(session) == {2}
    assert "выбывает" in summary
    assert "погиб" in prompt
    assert all_dead is False


def test_natural_one_always_misses_and_natural_twenty_crits(monkeypatch):
    session = _session(hp=14, max_hp=14, ac=9, heal_used=True)
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: 1)
    summary, _prompt, _all_dead = combat._resolve_enemy_attack(
        session,
        {"target_user_id": 1, "power": "DEADLY", "reason": "босс бьёт"},
    )
    assert "— мимо" in summary
    assert session.character_sheets["1"]["hp"] == 14

    values = iter([20, 3, 4])
    monkeypatch.setattr(combat.random, "randint", lambda _a, _b: next(values))
    summary, _prompt, _all_dead = combat._resolve_enemy_attack(
        session,
        {"target_user_id": 1, "power": "LOW", "reason": "крыса кусает"},
    )
    assert "КРИТ" in summary
    assert "Урон: 7" in summary
    assert session.character_sheets["1"]["hp"] == 7
