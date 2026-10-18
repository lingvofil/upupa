import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401

from AI import dnd_progression as progression

ROOT = Path(__file__).resolve().parents[1]
STORY = "Алина перепрыгивает пропасть, хватается за цепь и тащит Чудо за собой к выходу."


def _sheet(hp=12, con=13, **stats):
    base = {"STR": 16, "DEX": 14, "CON": con, "INT": 12, "WIS": 10, "CHA": 8}
    base.update(stats)
    return {"stats": dict(base), "base_stats": dict(base), "hp": hp, "max_hp": hp, "ac": 13, "status": "alive"}


def _session(**kwargs):
    values = dict(
        chat_id=-1, mode="participants",
        participants={"1": {"user_id": 1, "name": "Алина", "active": True},
                      "2": {"user_id": 2, "name": "Чудо", "active": True}},
        character_profiles={"1": {"style": "a"}, "2": {"style": "b"}},
        character_sheets={"1": _sheet(), "2": _sheet()},
        enemy_combatants={}, scene_clocks={}, growth_counts={}, local_enemy_rules={},
        mission_outcome="pending", inventories={},
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _no_archive(monkeypatch):
    monkeypatch.setattr(progression, "archived_progress", lambda session, user_id: None)


def test_level_table_and_bonuses():
    assert progression.level_for_xp(0) == 1
    assert progression.level_for_xp(progression.LEVEL_THRESHOLDS[1] - 1) == 1
    assert progression.level_for_xp(progression.LEVEL_THRESHOLDS[1]) == 2
    assert progression.level_for_xp(10**6) == progression.MAX_LEVEL
    assert progression.next_threshold(progression.MAX_LEVEL) is None
    assert [progression.attack_bonus_for_level(level) for level in (1, 4, 5, 8, 9)] == [0, 0, 1, 1, 2]
    assert progression.hp_bonus_for_level(3, 14) == 2 * 3  # CON +2 → +3 per level
    assert progression.hp_bonus_for_level(3, 13) == 2 * 2  # CON +1 → +2 per level
    assert progression.hp_bonus_for_level(2, 8) == 1  # CON -1 → +1
    assert progression.hp_bonus_for_level(2, 1) == 1  # never below +1 per level
    assert progression.hp_bonus_for_level(10, 20) == 9 * 4  # capped CON → +4


def test_first_observation_is_baseline_and_awards_nothing():
    session = _session(enemy_combatants={"огр": {"name": "Огр", "hp": 0, "status": "dead", "power": "HIGH"}},
                       mission_outcome="success")
    assert progression.account(session, story=STORY) == []
    assert all(row["xp"] == 0 for row in session.hero_progress.values())


def test_scene_xp_only_for_active_living_heroes_and_short_text_is_not_a_scene():
    session = _session()
    progression.account(session)
    session.character_sheets["2"]["hp"] = 0
    session.character_sheets["2"]["status"] = "dead"
    progression.account(session, story="ок")
    progression.account(session, story=STORY)
    assert session.hero_progress["1"]["xp"] == progression.SCENE_XP
    assert session.hero_progress["2"]["xp"] == 0


def test_enemy_defeat_is_credited_once_by_power_and_retreat_gives_half():
    session = _session(enemy_combatants={
        "огр": {"name": "Огр", "hp": 10, "status": "alive", "power": "HIGH"},
        "крыса": {"name": "Крыса", "hp": 3, "status": "alive", "power": "LOW"},
    })
    progression.account(session)
    session.enemy_combatants["огр"].update(hp=0, status="dead")
    session.local_enemy_rules = {"крыса": {"retreated": True}}
    progression.account(session)
    progression.account(session)  # replayed observation awards nothing more
    expected = progression.ENEMY_XP["HIGH"] + progression.ENEMY_XP["LOW"] // 2
    assert session.hero_progress["1"]["xp"] == expected == session.hero_progress["2"]["xp"]

    # A new fight reusing the key is credited again once it is fought anew.
    session.enemy_combatants["огр"].update(hp=8, status="alive")
    progression.account(session)
    session.enemy_combatants["огр"].update(hp=0, status="dead")
    progression.account(session)
    assert session.hero_progress["1"]["xp"] == expected + progression.ENEMY_XP["HIGH"]


def test_progress_clock_growth_and_mission_are_milestones():
    session = _session(scene_clocks={"побег": {"kind": "PROGRESS", "value": 2, "max": 4},
                                     "тревога": {"kind": "DANGER", "value": 1, "max": 4}})
    progression.account(session)
    session.scene_clocks["побег"]["value"] = 4
    session.scene_clocks["тревога"]["value"] = 4  # danger clocks are not rewards
    session.growth_counts = {"1": {"ESCAPE": 1}}
    session.mission_outcome = "success"
    progression.account(session)
    progression.account(session)
    first = progression.CLOCK_XP + progression.GROWTH_XP + progression.MISSION_XP["success"]
    assert session.hero_progress["1"]["xp"] == first
    assert session.hero_progress["2"]["xp"] == progression.CLOCK_XP + progression.MISSION_XP["success"]


def test_level_up_notice_heals_raises_max_hp_and_grants_asi_at_level_four():
    session = _session()
    progression.account(session)
    session.character_sheets["1"]["hp"] = 5
    session.hero_progress["1"]["xp"] = progression.LEVEL_THRESHOLDS[3] - progression.SCENE_XP
    notices = progression.account(session, story=STORY)
    row = session.hero_progress["1"]
    assert row["level"] == 4 and row["asi_pending"] == 1
    sheet = session.character_sheets["1"]
    gained = progression.hp_bonus_for_level(4, 13)
    assert sheet["max_hp"] == 12 + gained
    assert sheet["hp"] == 5 + gained
    assert len(notices) == 1 and "4 уровня" in notices[0] and "повысить характеристику" in notices[0]


def test_ability_increase_goes_through_effective_stats_and_keeps_base_array(monkeypatch):
    from AI import dnd_artifact_stats as artifact_stats

    monkeypatch.setattr(artifact_stats, "_STAT_BONUS_SOURCES", [progression.level_stat_bonuses])
    session = _session()
    progression.account(session)
    session.hero_progress["1"].update(xp=progression.LEVEL_THRESHOLDS[3], level=4, asi_pending=1)
    text = progression.choose_ability_increase(session, 1, "DEX")
    sheet = session.character_sheets["1"]
    assert "Ловкость +2" in text
    assert sheet["stats"]["DEX"] == 16 and sheet["base_stats"]["DEX"] == 14
    with pytest.raises(ValueError):
        progression.choose_ability_increase(session, 1, "DEX")


def test_ability_increase_respects_cap():
    session = _session(character_sheets={"1": _sheet(STR=19), "2": _sheet()})
    progression.account(session)
    session.hero_progress["1"].update(level=8, asi_pending=2)
    assert "Сила +1" in progression.choose_ability_increase(session, 1, "STR")
    with pytest.raises(ValueError):
        progression.choose_ability_increase(session, 1, "STR")


def test_clean_progress_never_grants_more_asi_than_level_allows():
    row = progression._clean_progress({"xp": progression.LEVEL_THRESHOLDS[3], "asi_pending": 5,
                                       "ability_increases": {"STR": 2}})
    assert row["level"] == 4 and row["asi_pending"] == 0


def test_archive_round_trip_respects_death_and_new_profile(monkeypatch):
    monkeypatch.undo()
    from AI import dnd_campaign as campaign
    from AI import dnd_death_legacy

    history = {"profile": {"style": "a"}, "progress": {"xp": 800, "level": 3, "ability_increases": {}}}
    monkeypatch.setattr(campaign, "_player_history", lambda chat_id, user_id: history)
    monkeypatch.setattr(dnd_death_legacy, "history_is_dead", lambda *_args: False)
    session = _session()
    assert progression.archived_progress(session, 1)["level"] == 3

    session.character_profiles["1"] = {"style": "совсем другой герой"}
    assert progression.archived_progress(session, 1) is None

    session.character_profiles["1"] = {"style": "a"}
    monkeypatch.setattr(dnd_death_legacy, "history_is_dead", lambda *_args: True)
    assert progression.archived_progress(session, 1) is None

    players = {"1": {"name": "Алина"}}
    session.hero_progress = {"1": {"xp": 600, "level": 3, "ability_increases": {"CON": 2}, "asi_pending": 0}}
    progression.archive_progress(session, players)
    assert players["1"]["progress"] == {"xp": 600, "level": 3, "ability_increases": {"CON": 2}, "asi_pending": 0}


def test_level_attack_bonus_is_added_to_proficiency(monkeypatch):
    from AI import dnd_combat as combat
    from AI import dnd_player_combat as player_combat

    session = _session(enemy_combatants={"огр": {"name": "Огр", "hp": 20, "max_hp": 20, "ac": 30,
                                                "status": "alive", "power": "HIGH"}})
    session.hero_progress = {"1": {"xp": progression.LEVEL_THRESHOLDS[8], "level": 9}}
    pending = {"attack": {"enemy_key": "огр", "weapon": "меч", "style": "MELEE"}}
    summary, _prompt = player_combat._resolve_attack_mechanics(combat, session, 1, pending, 10)
    # STR 16 -> +3, sword proficiency +2, level 9 +2 => 10 + 3 + 4 = 17
    assert "= 17 против КБ 30" in summary


def test_report_and_hero_lines():
    session = _session()
    session.hero_progress = {"1": {"xp": 900, "level": 3, "campaign_xp": 300, "start_level": 2}}
    assert "Алина: +300 опыта (ур. 2 → 3)" in progression.report_section(session)
    lines = progression.progress_lines(session.hero_progress["1"])
    assert lines[0].startswith("⭐ Уровень 3") and "900/1350" in lines[1]


def test_composed_runtime_awards_xp_and_announces_level_through_metadata(tmp_path):
    script = textwrap.dedent(r'''
        import json, pathlib, sys
        from tests import test_smoke_imports
        from AI import dnd, dnd_campaign as campaign
        from AI.dnd_runtime import configure_dnd_runtime
        from AI.dnd_style import configure_dnd_style

        dnd.ALLOWED_CHAT_ID = -9001
        dnd.DND_STATE_PATH = pathlib.Path(sys.argv[1]) / "state.json"
        configure_dnd_runtime()
        configure_dnd_style()
        session = dnd.GameSession(-9001, "Ведущий", starter_user_id=1, active_model="groq")
        session.mode = "participants"
        session.state = "WAITING_ACTION"
        session.participants = {"1": {"user_id": 1, "name": "Алина", "active": True}}
        campaign._ensure(session)
        session.character_sheets = {"1": {"stats": {"STR": 16, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
                                          "hp": 12, "max_hp": 12, "ac": 13, "status": "alive"}}
        dnd.dnd_sessions[-9001] = session
        story = "Алина вышибает дверь плечом, хватает фонарь и вытаскивает Чудо из горящей комнаты."
        campaign._apply_metadata(session, story + " [ACTION:INPUT]")  # baseline
        from AI import dnd_progression as progression
        session.hero_progress["1"]["xp"] = progression.LEVEL_THRESHOLDS[1] - progression.SCENE_XP
        clean, notices = campaign._apply_metadata(session, story + " [ACTION:INPUT]")
        print(json.dumps({"notices": notices, "xp": session.hero_progress["1"]["xp"],
                          "level": session.hero_progress["1"]["level"],
                          "max_hp": session.character_sheets["1"]["max_hp"]}, ensure_ascii=False))
    ''')
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-4000:]
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["xp"] == progression.LEVEL_THRESHOLDS[1]
    assert data["level"] == 2
    assert data["max_hp"] == 12 + progression.hp_bonus_for_level(2, 13)
    assert any("2 уровня" in notice for notice in data["notices"])
