from AI import dnd_combat as combat
from AI.dnd_enemy_stats import NPC_STATS_RULES, decorate_attack_result, enrich_attack


def test_enemy_attack_tag_exposes_hp_and_ac():
    text = (
        "[ACTION:ENEMY_ATTACK;TARGETS:123;POWER:HIGH;ENEMY:Орк-мясник;HP:31;AC:15;"
        "REASON:орк рубит героя тесаком]"
    )
    base = combat._parse_attack(text)

    attack = enrich_attack(combat, text, base)

    assert attack["enemy_name"] == "Орк-мясник"
    assert attack["enemy_hp"] == 31
    assert attack["enemy_ac"] == 15


def test_enemy_stats_use_power_defaults_when_model_omits_values():
    text = "[ACTION:ENEMY_ATTACK;TARGETS:123;POWER:LOW;REASON:крыса кусает]"
    attack = enrich_attack(combat, text, combat._parse_attack(text))

    assert attack["enemy_name"] == "Враг"
    assert attack["enemy_hp"] == 8
    assert attack["enemy_ac"] == 10


def test_enemy_stat_line_is_prepended_to_attack_result():
    attack = {"enemy_name": "Гоблин", "enemy_hp": 12, "enemy_ac": 11}
    summary, prompt, all_dead = decorate_attack_result(
        attack,
        ("Бросок врага: 7 — мимо.", "Продолжай сюжет.", False),
    )

    assert summary.startswith("👹 Гоблин — ❤️ 12 HP · 🛡 КБ 11\n")
    assert prompt.startswith("Нападающий Гоблин: HP 12, КБ 11.")
    assert all_dead is False


def test_prompt_requires_attacker_name_hp_and_ac():
    assert "ENEMY:орк" in NPC_STATS_RULES
    assert "HP:18" in NPC_STATS_RULES
    assert "AC:13" in NPC_STATS_RULES
