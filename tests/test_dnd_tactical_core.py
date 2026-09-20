from types import SimpleNamespace

from AI import dnd_conditions as conditions
from AI import dnd_scene_tactics as scene_tactics
from AI import dnd_special_moves as special_moves


def _session():
    return SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Алиса"},
            "2": {"user_id": 2, "name": "Боря"},
        },
        character_profiles={
            "1": {"special": "аварийный план из кармана"},
            "2": {"special": "грязный трюк без инструкции"},
        },
        scene_count=3,
        state="WAITING_ACTION",
        pending_roll=None,
    )


def test_condition_metadata_deduplicates_same_mechanical_effect():
    session = _session()
    text = (
        "[CONDITION:ADD;PLAYER:1;NAME:подвернул ногу;EFFECT:MOVE_DISADVANTAGE;"
        "CLEAR:перевязать;SCENES:2]"
    )
    cleaned, notices = conditions.apply_condition_metadata(session, text, text, [])
    assert cleaned == ""
    assert len(notices) == 1
    assert session.conditions["1"][0]["name"] == "подвернул ногу"

    replacement = (
        "[CONDITION:ADD;PLAYER:1;NAME:ботинок проклят;EFFECT:MOVE_DISADVANTAGE;"
        "CLEAR:снять ботинок;SCENES:1]"
    )
    conditions.apply_condition_metadata(session, replacement, replacement, [])
    assert len(session.conditions["1"]) == 1
    assert session.conditions["1"][0]["name"] == "ботинок проклят"


def test_condition_penalty_is_domain_specific_and_cancels_advantage():
    session = _session()
    session.conditions = {
        "1": [{
            "name": "подвернул ногу",
            "effect": "MOVE_DISADVANTAGE",
            "clear": "перевязать",
            "scenes_remaining": None,
            "uses_remaining": None,
            "skip_scene_tick": None,
        }]
    }
    move = (
        "Прыжок. "
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:перепрыгнуть пролом;"
        "DC:12;MODE:ADVANTAGE;TARGETS:1]"
    )
    guarded, consumed = conditions.apply_condition_penalties(session, move)
    assert "MODE:NORMAL" in guarded
    assert consumed == []

    social = (
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:SOCIAL;REASON:убедить охранника;"
        "DC:12;MODE:NORMAL;TARGETS:1]"
    )
    guarded, _ = conditions.apply_condition_penalties(session, social)
    assert "MODE:NORMAL" in guarded
    assert "DISADVANTAGE" not in guarded


def test_scene_duration_counts_scenes_not_messages():
    session = _session()
    text = (
        "[CONDITION:ADD;PLAYER:1;NAME:в глазах двоится;"
        "EFFECT:PERCEPTION_DISADVANTAGE;CLEAR:промыть глаза;SCENES:2]"
    )
    conditions.apply_condition_metadata(session, text, text, [])
    assert session.conditions["1"][0]["scenes_remaining"] == 2

    session.scene_count = 4
    conditions.advance_condition_scenes(session)
    assert session.conditions["1"][0]["scenes_remaining"] == 2

    session.scene_count = 5
    conditions.advance_condition_scenes(session)
    assert session.conditions["1"][0]["scenes_remaining"] == 1

    session.scene_count = 6
    conditions.advance_condition_scenes(session)
    assert session.conditions["1"] == []


def test_cinematic_attack_requires_available_scene_object():
    session = _session()
    scene_tactics.apply_scene_metadata(
        session,
        "[SCENE:UPSERT;ID:люстра;NAME:люстра;STATE:висит над врагом]",
        "[SCENE:UPSERT;ID:люстра;NAME:люстра;STATE:висит над врагом]",
        [],
    )
    valid = (
        "[ACTION:CINEMATIC_ATTACK;TARGETS:1;ENEMY:огр;POWER:HIGH;HP:28;AC:14;"
        "ABILITY:STR;DC:16;MODE:NORMAL;OBJECT:люстра;METHOD:обрушить люстру;REASON:рублю цепь]"
    )
    assert scene_tactics.validate_cinematic_object(session, valid) == (True, "люстра")

    scene_tactics.consume_scene_object(session, "люстра", "обрушить на огра")
    assert session.scene_objects["люстра"]["available"] is False
    assert scene_tactics.validate_cinematic_object(session, valid) == (False, "люстра")


def test_cinematic_attack_can_establish_plausible_object_in_same_response():
    session = _session()
    response = (
        "[SCENE:UPSERT;ID:бочка;NAME:бочка масла;STATE:стоит у лестницы]"
        "[ACTION:CINEMATIC_ATTACK;TARGETS:1;ENEMY:огр;POWER:HIGH;HP:28;AC:14;"
        "ABILITY:DEX;DC:16;MODE:NORMAL;OBJECT:бочка;METHOD:поджечь и столкнуть;REASON:толкаю бочку]"
    )
    assert scene_tactics.validate_cinematic_object(session, response) == (True, "бочка")


def test_enemy_intent_tracks_response_window_without_wall_clock():
    session = _session()
    text = (
        "[INTENT:SET;ENEMY:Мясник;ACTION:обрушить колонну;TARGETS:1,2;"
        "DANGER:DEADLY;DETAIL:под угрозой балкон]"
    )
    scene_tactics.apply_scene_metadata(session, text, text, [])
    assert session.enemy_intent["due"] is False
    assert session.enemy_intent["target_user_ids"] == [1, 2]

    clear = "[INTENT:CLEAR;REASON:цепь колонны перерублена]"
    cleaned, notices = scene_tactics.apply_scene_metadata(session, clear, clear, [])
    assert cleaned == ""
    assert session.enemy_intent is None
    assert "сорвано" in notices[0]


def test_special_move_is_offered_only_by_explicit_action_marker():
    response = (
        "[ACTION:ROLL;TYPE:CHECK;DOMAIN:MOVE;REASON:прыгнуть;DC:12;"
        "MODE:NORMAL;TARGETS:1;SPECIAL:1]"
    )
    assert special_moves.special_eligible_user(response) == 1
    assert special_moves.special_eligible_user(response.replace(";SPECIAL:1", "")) is None


def test_special_move_requires_owner_confirmation_and_does_not_spend_charge_yet():
    session = _session()
    session.state = "WAITING_ROLL"
    session.pending_roll = {
        "type": "CHECK",
        "mode": "DISADVANTAGE",
        "target_user_ids": [1],
        "reason": "аварийный план",
    }
    session.special_move_charges = {"1": 1, "2": 1}
    session.special_move_offer_user_id = 1
    session.special_move_pending_user_id = None

    ok, _ = special_moves.activate_special(session, 2)
    assert ok is False
    assert session.special_move_charges["1"] == 1

    ok, text = special_moves.activate_special(session, 1)
    assert ok is True
    assert "активирован" in text
    assert session.pending_roll["mode"] == "NORMAL"
    assert session.special_move_pending_user_id == 1
    assert session.special_move_charges["1"] == 1
