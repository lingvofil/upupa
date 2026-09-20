from types import SimpleNamespace

from AI import dnd_scene_clocks as clocks
from AI import dnd_weakness_luck as luck


def _session():
    return SimpleNamespace(
        participants={"1": {"user_id": 1, "name": "Алиса"}},
        character_profiles={
            "1": {
                "style": "авантюрист",
                "strength": "не теряется",
                "weakness": "лезет проверять запретное",
                "special": "аварийный план",
            }
        },
        luck_tokens={},
        weakness_luck_earned={},
        weakness_luck_spent={},
        pending_weakness_invocations={},
        scene_clocks={},
        conditions={},
        threat={"name": None, "level": 0, "max": 6, "history": []},
        scene_count=2,
    )


def _invoke(session):
    session.pending_weakness_invocations["1"] = {
        "weakness": "лезет проверять запретное",
        "action": "играю слабость — открываю запретный люк",
        "scene": 2,
    }


def test_explicit_weakness_phrase_is_required():
    assert luck._weakness_invoked("играю слабость — открываю люк") is True
    assert luck._weakness_invoked("поддаюсь своей слабости и открываю люк") is True
    assert luck._weakness_invoked("моя слабость — люблю люки") is False


def test_roll_weakness_worsens_normal_mode_and_reserves_reward():
    session = _session()
    _invoke(session)
    response = (
        "[WEAKNESS:ROLL;PLAYER:1;COMPLICATION:любопытство заставляет лезть первым]"
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:спуститься в люк;"
        "DC:11;MODE:NORMAL;TARGETS:1]"
    )

    guarded, reward = luck._apply_roll_tag(session, response)

    assert "[WEAKNESS:" not in guarded
    assert "MODE:DISADVANTAGE" in guarded
    assert reward == {
        "player": 1,
        "complication": "любопытство заставляет лезть первым",
        "mode": "DISADVANTAGE",
    }


def test_roll_weakness_cancels_advantage_but_cannot_stack_beyond_disadvantage():
    session = _session()
    _invoke(session)
    response = (
        "[WEAKNESS:ROLL;PLAYER:1;COMPLICATION:лезет не подумав]"
        "[ACTION:PLAYER_ATTACK;TARGETS:1;ENEMY:огр;POWER:MEDIUM;HP:16;AC:12;"
        "WEAPON:стул;STYLE:MELEE;MODE:ADVANTAGE;REASON:бьёт огра]"
    )
    guarded, reward = luck._apply_roll_tag(session, response)
    assert "MODE:NORMAL" in guarded
    assert reward is not None

    response = (
        "[WEAKNESS:ROLL;PLAYER:1;COMPLICATION:лезет не подумав]"
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:прыжок;DC:12;"
        "MODE:DISADVANTAGE;TARGETS:1]"
    )
    guarded, reward = luck._apply_roll_tag(session, response)
    assert "MODE:DISADVANTAGE" in guarded
    assert reward is None


def test_immediate_danger_cost_awards_luck_only_after_state_changes():
    session = _session()
    _invoke(session)
    clocks._set_clock(
        session,
        {
            "ID": "alarm",
            "NAME": "Тревога",
            "KIND": "DANGER",
            "VALUE": "2",
            "MAX": "6",
            "WHEN_FULL": "прибывает стража",
        },
    )
    tag = (
        "[WEAKNESS:PAYOFF;PLAYER:1;COST:DANGER_PLUS_1;"
        "COMPLICATION:Алиса шумно вскрывает запретный люк]"
    )

    cleaned, notices = luck.apply_weakness_metadata(session, tag, tag, [])

    assert cleaned == ""
    assert session.scene_clocks["alarm"]["value"] == 3
    assert session.luck_tokens["1"] == 1
    assert session.weakness_luck_earned["1"] == 1
    assert "1" not in session.pending_weakness_invocations
    assert any("жетон удачи" in notice for notice in notices)


def test_immediate_cost_without_real_state_change_does_not_award():
    session = _session()
    _invoke(session)
    tag = (
        "[WEAKNESS:PAYOFF;PLAYER:1;COST:DANGER_PLUS_1;"
        "COMPLICATION:шумит там где опасности нет]"
    )
    _, notices = luck.apply_weakness_metadata(session, tag, tag, [])
    assert session.luck_tokens.get("1", 0) == 0
    assert session.weakness_luck_earned.get("1", 0) == 0
    assert notices == []


def test_condition_cost_must_be_new_to_count_as_real_complication():
    session = _session()
    _invoke(session)
    tag = (
        "[WEAKNESS:PAYOFF;PLAYER:1;COST:CONDITION;NAME:залип на загадке;"
        "EFFECT:PERCEPTION_DISADVANTAGE;CLEAR:товарищ оттаскивает;USES:1;"
        "COMPLICATION:не может смотреть ни на что кроме головоломки]"
    )
    luck.apply_weakness_metadata(session, tag, tag, [])
    assert session.luck_tokens["1"] == 1
    assert session.conditions["1"][0]["effect"] == "PERCEPTION_DISADVANTAGE"

    session.luck_tokens["1"] = 0
    _invoke(session)
    _, notices = luck.apply_weakness_metadata(session, tag, tag, [])
    assert session.luck_tokens["1"] == 0
    assert not any("жетон удачи" in notice for notice in notices)


def test_progress_cost_requires_existing_progress_to_remove():
    session = _session()
    _invoke(session)
    clocks._set_clock(
        session,
        {
            "ID": "access",
            "NAME": "Доступ",
            "KIND": "PROGRESS",
            "VALUE": "1",
            "MAX": "6",
            "WHEN_FULL": "дверь открыта",
        },
    )
    tag = (
        "[WEAKNESS:PAYOFF;PLAYER:1;COST:PROGRESS_MINUS_1;"
        "COMPLICATION:Алиса отвлеклась на блестящую ерунду]"
    )
    luck.apply_weakness_metadata(session, tag, tag, [])
    assert session.scene_clocks["access"]["value"] == 0
    assert session.luck_tokens["1"] == 1


def test_luck_balance_is_one_and_earning_cap_is_two_per_adventure():
    session = _session()
    first = luck._award_luck(session, 1, "первое осложнение")
    second = luck._award_luck(session, 1, "второе осложнение")
    assert first is not None
    assert second is None
    assert session.luck_tokens["1"] == 1

    assert luck._spend_luck(session, 1) is True
    third = luck._award_luck(session, 1, "второе заработанное осложнение")
    assert third is not None
    assert session.weakness_luck_earned["1"] == 2
    assert luck._spend_luck(session, 1) is True
    assert luck._award_luck(session, 1, "третье осложнение") is None


def test_luck_improves_roll_exactly_one_step():
    assert luck._improve_mode("DISADVANTAGE") == "NORMAL"
    assert luck._improve_mode("NORMAL") == "ADVANTAGE"
    assert luck._improve_mode("ADVANTAGE") is None


def test_new_adventure_resets_tokens_and_limits():
    session = _session()
    session.luck_tokens = {"1": 1}
    session.weakness_luck_earned = {"1": 2}
    session.weakness_luck_spent = {"1": 1}
    _invoke(session)

    luck._reset_adventure(session)

    assert session.luck_tokens == {"1": 0}
    assert session.weakness_luck_earned == {"1": 0}
    assert session.weakness_luck_spent == {"1": 0}
    assert session.pending_weakness_invocations == {}


def test_luck_line_is_visible_and_bounded():
    session = _session()
    session.luck_tokens = {"1": 1}
    session.weakness_luck_earned = {"1": 2}
    assert luck._luck_line(session, 1) == "🍀 Жетон удачи: 1/1 · заработано 2/2 за приключение"
