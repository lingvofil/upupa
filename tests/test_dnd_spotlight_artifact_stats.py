from types import SimpleNamespace

from AI.dnd_artifact_stats import artifact_bonuses, sync_character_sheet
from AI.dnd_roll_ability_display import annotate_roll_ability
from AI.dnd_spotlight import enforce_spotlight, next_spotlight


def _session():
    return SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "А"},
            "2": {"user_id": 2, "name": "Б"},
            "3": {"user_id": 3, "name": "В"},
        },
        character_sheets={
            "1": {"hp": 10, "status": "alive"},
            "2": {"hp": 10, "status": "alive"},
            "3": {"hp": 10, "status": "alive"},
        },
        spotlight_order=[1, 2, 3],
        spotlight_cursor=0,
        spotlight_individual_streak=0,
        spotlight_decisions_since_poll=3,
        spotlight_last_player=None,
    )


def test_spotlight_preserves_explicit_target_and_keeps_expected_player_queued():
    session = _session()

    first, consumed, rewritten = enforce_spotlight(session, "Сцена [ACTION:INPUT;TARGETS:1]")
    assert first.endswith("[ACTION:INPUT;TARGETS:1]")
    assert consumed == 1
    assert rewritten is False
    assert next_spotlight(session) == 2

    second, consumed, rewritten = enforce_spotlight(session, "Ещё сцена [ACTION:INPUT;TARGETS:1]")
    assert second.endswith("[ACTION:INPUT;TARGETS:1]")
    assert "TARGETS:2" not in second
    assert consumed is None
    assert rewritten is False
    assert next_spotlight(session) == 2

    third, consumed, rewritten = enforce_spotlight(
        session,
        "Следующая проверка [ACTION:ROLL;TYPE:CHECK;SKILL:Ловкость рук;REASON:найти вещи;DC:12;MODE:NORMAL]",
    )
    assert "TARGETS:2" in third
    assert consumed == 2
    assert rewritten is True
    assert next_spotlight(session) == 3


def test_spotlight_does_not_retarget_story_bound_roll():
    session = _session()
    session.spotlight_cursor = 1
    response = (
        "Детектор, у тебя из карманов всё высыпалось. "
        "[ACTION:ROLL;TYPE:CHECK;TARGETS:1;SKILL:Ловкость рук;REASON:найти потерянные предметы;DC:12;MODE:NORMAL]"
    )

    guarded, consumed, rewritten = enforce_spotlight(session, response)

    assert guarded == response
    assert "TARGETS:1" in guarded
    assert "TARGETS:2" not in guarded
    assert consumed is None
    assert rewritten is False
    assert next_spotlight(session) == 2


def test_spotlight_assigns_untargeted_check_but_not_save():
    session = _session()
    checked, consumed, rewritten = enforce_spotlight(
        session,
        "Проверка [ACTION:ROLL;TYPE:CHECK;SKILL:Атлетика;REASON:поднять люк;DC:10;MODE:NORMAL]",
    )
    assert "TARGETS:1" in checked
    assert consumed == 1
    assert rewritten is True
    assert next_spotlight(session) == 2

    saved, consumed, rewritten = enforce_spotlight(
        session,
        "Опасность [ACTION:ROLL;TYPE:SAVE;TARGETS:1;REASON:увернуться от камня;DC:10;MODE:NORMAL]",
    )
    assert "TARGETS:1" in saved
    assert consumed is None
    assert rewritten is False
    assert next_spotlight(session) == 2


def test_group_turn_does_not_consume_spotlight():
    session = _session()
    response, consumed, rewritten = enforce_spotlight(session, "Все решают [ACTION:INPUT]")
    assert response.endswith("[ACTION:INPUT]")
    assert consumed is None
    assert rewritten is False
    assert next_spotlight(session) == 1
    assert session.spotlight_individual_streak == 0


def test_dead_player_is_skipped_in_spotlight_order():
    session = _session()
    session.character_sheets["1"]["hp"] = 0
    session.character_sheets["1"]["status"] = "dead"
    assert next_spotlight(session) == 2


def test_artifact_bonus_changes_effective_stat_and_respects_cap():
    session = SimpleNamespace(
        inventories={
            "1": [
                {"name": "Сапоги", "kind": "artifact", "stat": "DEX", "stat_bonus": 2},
                {"name": "Кольцо", "kind": "artifact", "stat": "DEX", "stat_bonus": 2},
                {"name": "Ложка", "kind": "item", "stat": "STR", "stat_bonus": 2},
            ]
        },
        character_sheets={
            "1": {
                "stats": {"STR": 16, "DEX": 18, "CON": 14, "INT": 13, "WIS": 12, "CHA": 10},
                "hp": 12,
                "max_hp": 12,
                "ac": 12,
                "status": "alive",
            }
        },
    )

    assert artifact_bonuses(session, 1) == {"DEX": 4}
    assert sync_character_sheet(session, 1) is True
    sheet = session.character_sheets["1"]
    assert sheet["base_stats"]["DEX"] == 18
    assert sheet["stats"]["DEX"] == 20
    assert sheet["artifact_stat_bonuses"] == {"DEX": 2}

    session.inventories["1"] = []
    assert sync_character_sheet(session, 1) is True
    assert sheet["stats"]["DEX"] == 18
    assert sheet["artifact_stat_bonuses"] == {}


def test_roll_annotation_shows_skill_ability_before_throw():
    text, ability = annotate_roll_ability(
        "Прыгай [ACTION:ROLL;TYPE:CHECK;SKILL:Акробатика;REASON:перепрыгнуть щель;DC:11;MODE:NORMAL]"
    )
    assert ability == "DEX"
    assert "ABILITY:DEX" in text
    assert "REASON:перепрыгнуть щель · Ловкость" in text


def test_save_annotation_infers_visible_ability():
    text, ability = annotate_roll_ability(
        "Бах [ACTION:ROLL;TYPE:SAVE;REASON:увернуться от взрыва;DC:12;MODE:NORMAL;TARGETS:1]"
    )
    assert ability == "DEX"
    assert "ABILITY:DEX" in text
    assert "Ловкость" in text
