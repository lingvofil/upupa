"""Per-hero campaign statistics recorded by the mechanics and the finale awards."""
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401
from tests.test_dnd_local_engine import action, backend, session

from AI import dnd_adventure as adventure
from AI import dnd_campaign_stats as stats
from AI import dnd_combat as combat
from AI import dnd_local_engine as engine
from AI import dnd_turn_lifecycle as lifecycle


def party(**extra):
    values = dict(
        participants={"1": {"name": "Маша"}, "2": {"name": "Денис"}},
        character_sheets={"1": {"hp": 10, "max_hp": 14, "status": "alive"}, "2": {"hp": 0, "max_hp": 14, "status": "dead"}},
        inventories={"1": [{"name": "Корона", "quantity": 1}], "2": []},
        initial_inventories={"1": [], "2": []},
        reputations={"1": ["спас мэра", "сжёг мост"], "2": ["старый слух"]},
        campaign_stats_start={"1": 0, "2": 1},
        campaign_stats={},
    )
    values.update(extra)
    return SimpleNamespace(**values)


def test_awards_go_to_a_unique_leader_and_ties_get_nothing():
    current = party()
    stats.record(current, 1, damage=12, kills=2, nat1=1)
    stats.record(current, 2, damage=12, damage_taken=20, nat20=1)
    lines = stats.awards(current)
    text = "\n".join(lines)
    assert "Мясник" not in text  # 12 vs 12
    assert "«Добивала» — Маша" in text and "«Любимчик кубов» — Денис" in text
    assert "«Проклятый кубик» — Маша" in text and "«Живой щит» — Денис: принял на себя 20 урона" in text
    assert "«Барахольщик» — Маша: унёс новых вещей: 1" in text
    assert "«Народная молва» — Маша: новых слухов о себе: 2" in text  # Денис's old rumour doesn't count
    assert "«Пал смертью храбрых» — Денис" in text


def test_report_section_is_part_of_the_final_report():
    current = party(mission_outcome="success", mission_goal="Украсть корону", mission_evidence="корона у Маши")
    stats.record(current, 1, kills=1)
    stats.install_dnd_campaign_stats(state_policy=SimpleNamespace(
        add_ensure_hook=lambda hook: None, add_state_field=lambda name, getter: None))
    assert "🏆 Награды партии" in adventure.adventure_report(current)


def test_d20_record_is_safe_with_odd_dc_values():
    current = party()
    stats.record_d20(current, 1, 20, total=20, dc="высокая")
    stats.record_d20(current, 1, 1, total=3, dc=10)
    row = current.campaign_stats["1"]
    assert row["rolls"] == 2 and row["nat20"] == 1 and row["nat1"] == 1 and "successes" not in row


def test_enemy_attack_records_damage_taken_by_its_target(monkeypatch):
    current = SimpleNamespace(participants={"1": {"name": "Маша", "active": True}},
                              character_sheets={"1": {"hp": 10, "max_hp": 14, "ac": 5, "status": "alive"}},
                              campaign_stats={})
    rolls = iter([15, 4])
    monkeypatch.setattr(combat.random, "randint", lambda low, high: next(rolls))
    combat._resolve_enemy_attack(current, {"target_user_id": 1, "power": "MEDIUM", "reason": "Огр бьёт"})
    assert current.campaign_stats["1"]["damage_taken"] == 4


def test_local_attack_commits_hits_damage_and_kill_only_with_the_action():
    sess = session()
    sess.enemy_combatants["огр"]["hp"] = 3
    sess.state = "WAITING_ROLL"
    sess.pending_roll = {"type": "ATTACK", "mode": "NORMAL", "target_user_ids": [1],
                         "attack": {"enemy_key": "огр", "weapon": "меч", "style": "MELEE"}}
    lifecycle.sync_window(sess)
    be, _calls = backend(18, 6, 6)
    import AI.dnd_combat as real_combat
    be.combat.ability_modifier = real_combat.ability_modifier
    engine.execute_action(sess, action(sess, target_id="огр"), backend=be, ai_mode="economy")
    row = sess.campaign_stats["1"]
    assert row["hits"] == 1 and row["kills"] == 1 and row["damage"] == 3 and row["rolls"] == 1
