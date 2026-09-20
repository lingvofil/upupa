from types import SimpleNamespace

from AI import dnd_conditions as conditions
from AI import dnd_growth as growth
from AI import dnd_item_actions as item_actions
from AI import dnd_weakness_luck as weakness


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
        conditions={},
        item_boosts={},
        achievement_boosts={},
        pending_weakness_invocations={
            "1": {
                "weakness": "лезет проверять запретное",
                "action": "играю слабость — открываю запретный люк",
                "scene": 1,
            }
        },
        luck_tokens={},
        weakness_luck_earned={},
        weakness_luck_spent={},
        pending_roll=None,
    )


def _condition(session, effect="NEXT_ROLL_DISADVANTAGE", uses=1):
    session.conditions["1"] = [{
        "name": "мешает действовать",
        "effect": effect,
        "clear": "пережить",
        "scenes_remaining": None,
        "uses_remaining": uses,
        "skip_scene_tick": None,
    }]


def _roll(mode="NORMAL", domain="MOVE"):
    return (
        "[WEAKNESS:ROLL;PLAYER:1;COMPLICATION:любопытство тащит Алису первой]"
        f"[ACTION:ROLL;TYPE:CHECK;DOMAIN:{domain};REASON:опасное действие;"
        f"DC:12;MODE:{mode};TARGETS:1]"
    )


def _action_mode(text):
    match = conditions._ACTION_RE.search(text)
    return conditions._mode(match.group(2) if match else "")


def test_existing_condition_prevents_free_weakness_luck():
    session = _session()
    _condition(session)

    guarded, reward = weakness._apply_roll_tag(session, _roll("NORMAL"))

    assert reward is None
    assert _action_mode(guarded) == "NORMAL"

    final, consumed = conditions.apply_condition_penalties(session, guarded)
    assert _action_mode(final) == "DISADVANTAGE"
    assert consumed == [("1", "NEXT_ROLL_DISADVANTAGE")]


def test_weakness_can_add_real_penalty_after_advantage_and_condition_cancel():
    session = _session()
    _condition(session)

    guarded, reward = weakness._apply_roll_tag(session, _roll("ADVANTAGE"))

    assert reward is not None
    assert _action_mode(guarded) == "NORMAL"

    final, consumed = conditions.apply_condition_penalties(session, guarded)
    assert _action_mode(final) == "DISADVANTAGE"
    assert consumed == [("1", "NEXT_ROLL_DISADVANTAGE")]

    baseline, _ = conditions.apply_condition_penalties(session, _roll("ADVANTAGE").split("[WEAKNESS:", 1)[0] + "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:опасное действие;DC:12;MODE:ADVANTAGE;TARGETS:1]")
    assert _action_mode(baseline) == "NORMAL"


def test_cinematic_attack_can_pay_for_weakness_luck():
    session = _session()
    response = (
        "[WEAKNESS:ROLL;PLAYER:1;COMPLICATION:лезет под падающий механизм первой]"
        "[ACTION:CINEMATIC_ATTACK;TARGETS:1;ENEMY:огр;POWER:HIGH;HP:28;AC:14;"
        "ABILITY:STR;DC:16;MODE:NORMAL;OBJECT:люстра;METHOD:уронить люстру;"
        "REASON:Алиса дёргает цепь]"
    )

    guarded, reward = weakness._apply_roll_tag(session, response)

    assert reward is not None
    assert _action_mode(guarded) == "DISADVANTAGE"


def test_condition_use_is_queued_until_actual_roll_commit():
    session = _session()
    _condition(session)

    guarded, consumed = conditions.apply_condition_penalties(
        session,
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:прыжок;DC:12;MODE:NORMAL;TARGETS:1]",
    )
    assert _action_mode(guarded) == "DISADVANTAGE"
    assert session.conditions["1"][0]["uses_remaining"] == 1

    session.pending_roll = {"type": "CHECK", "mode": "DISADVANTAGE", "target_user_ids": [1]}
    assert conditions._queue_pending_uses(session, consumed) is True
    assert session.conditions["1"][0]["uses_remaining"] == 1
    assert session.pending_roll["condition_uses_pending"] == [
        {"player": "1", "effect": "NEXT_ROLL_DISADVANTAGE"}
    ]

    assert conditions._consume_pending_uses(session, session.pending_roll) is True
    assert session.conditions["1"] == []
    assert "condition_uses_pending" not in session.pending_roll


def test_item_boost_is_not_spent_on_already_advantaged_roll():
    session = _session()
    session.item_boosts["1"] = {"domain": "MOVE", "source": "Дверная ручка"}
    response = "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:бег;DC:10;MODE:ADVANTAGE;TARGETS:1]"

    guarded, source = item_actions.apply_item_boost(session, response)

    assert source is None
    assert _action_mode(guarded) == "ADVANTAGE"
    assert session.item_boosts["1"]["source"] == "Дверная ручка"


def test_achievement_boost_is_not_spent_on_already_advantaged_roll():
    session = _session()
    session.achievement_boosts["1"] = {"domain": "MOVE", "source": "Профессиональный беглец"}
    response = "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:бег;DC:10;MODE:ADVANTAGE;TARGETS:1]"

    guarded, source = growth.apply_achievement_boost(session, response)

    assert source is None
    assert _action_mode(guarded) == "ADVANTAGE"
    assert session.achievement_boosts["1"]["source"] == "Профессиональный беглец"


def test_two_advantage_resources_do_not_both_burn_on_same_roll():
    session = _session()
    session.item_boosts["1"] = {"domain": "MOVE", "source": "Дверная ручка"}
    session.achievement_boosts["1"] = {"domain": "MOVE", "source": "Профессиональный беглец"}
    response = "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:бег;DC:10;MODE:NORMAL;TARGETS:1]"

    guarded, achievement_source = growth.apply_achievement_boost(session, response)
    guarded, item_source = item_actions.apply_item_boost(session, guarded)

    assert achievement_source == "Профессиональный беглец"
    assert item_source is None
    assert _action_mode(guarded) == "ADVANTAGE"
    assert "1" not in session.achievement_boosts
    assert session.item_boosts["1"]["source"] == "Дверная ручка"


def test_combined_advantage_weakness_and_condition_has_stable_result():
    session = _session()
    _condition(session)
    session.achievement_boosts["1"] = {"domain": "MOVE", "source": "Профессиональный беглец"}

    response, source = growth.apply_achievement_boost(session, _roll("NORMAL"))
    assert source == "Профессиональный беглец"
    assert _action_mode(response) == "ADVANTAGE"

    response, reward = weakness._apply_roll_tag(session, response)
    assert reward is not None
    assert _action_mode(response) == "NORMAL"

    response, consumed = conditions.apply_condition_penalties(session, response)
    assert _action_mode(response) == "DISADVANTAGE"
    assert consumed == [("1", "NEXT_ROLL_DISADVANTAGE")]
