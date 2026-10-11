"""Behaviour templates for opted-in simple enemies; deterministic, no AI."""
import json

from tests import test_smoke_imports  # noqa: F401
from tests.test_dnd_local_engine import Dice, backend, session

from AI import dnd_local_engine as engine
from AI import dnd_scene_rules as scene_rules
from AI import dnd_turn_lifecycle as lifecycle


def fight(**enemies):
    sess = session()
    sess.enemy_combatants = {key: {"name": key.capitalize(), "power": "MEDIUM", "hp": 20, "max_hp": 20, "ac": 12,
                                   "status": "alive", **row} for key, row in enemies.items()}
    sess.local_enemy_rules = {key: {"policy": "simple", "style": "brute", "retreat_below": 0.2} for key in enemies}
    lifecycle.close_window(sess)
    sess.character_sheets["1"]["hp"] = 5   # weakest
    sess.character_sheets["2"]["hp"] = 14  # sturdiest
    return sess


def styled(sess, key, style, **extra):
    sess.local_enemy_rules[key] = {"policy": "simple", "style": style,
                                   "retreat_below": scene_rules.ENEMY_STYLES[style]["retreat_below"], **extra}


def test_metadata_accepts_known_styles_with_their_retreat_defaults_and_rejects_unknown():
    sess = session()
    block = {"огр": {"policy": "simple", "style": "coward", "attack_text": "Огр машет дубиной"}}
    scene_rules.apply_enemy_rule_metadata(sess, f"<DND_ENEMIES>{json.dumps(block, ensure_ascii=False)}</DND_ENEMIES>", "", [])
    assert sess.local_enemy_rules["огр"]["style"] == "coward" and sess.local_enemy_rules["огр"]["retreat_below"] == 0.5
    block["огр"]["style"] = "dragon-lord"
    sess.local_enemy_rules = {}
    scene_rules.apply_enemy_rule_metadata(sess, f"<DND_ENEMIES>{json.dumps(block, ensure_ascii=False)}</DND_ENEMIES>", "", [])
    assert sess.local_enemy_rules == {}
    legacy = {"огр": {"policy": "simple", "retreat_below": 0.2}}
    scene_rules.apply_enemy_rule_metadata(sess, f"<DND_ENEMIES>{json.dumps(legacy)}</DND_ENEMIES>", "", [])
    assert sess.local_enemy_rules["огр"]["style"] == "brute"


def test_brute_hits_weakest_berserker_hits_sturdiest_and_rages_when_hurt():
    sess = fight(огр={})
    styled(sess, "огр", "brute")
    assert engine.choose_enemy_action(sess, "огр")["target_user_id"] == 1
    styled(sess, "огр", "berserker")
    decision = engine.choose_enemy_action(sess, "огр")
    assert decision["target_user_id"] == 2 and decision["power"] == "MEDIUM"
    sess.enemy_combatants["огр"]["hp"] = 3  # 15%: a berserker never retreats
    decision = engine.choose_enemy_action(sess, "огр")
    assert decision["kind"] == "attack" and decision["power"] == "HIGH"


def test_coward_flees_at_half_hp():
    sess = fight(гоблин={})
    styled(sess, "гоблин", "coward")
    sess.enemy_combatants["гоблин"]["hp"] = 11
    assert engine.choose_enemy_action(sess, "гоблин")["kind"] == "attack"
    sess.enemy_combatants["гоблин"]["hp"] = 10
    assert engine.choose_enemy_action(sess, "гоблин")["kind"] == "retreat"


def test_leader_commands_while_escort_lives_and_escort_focuses_the_mark():
    sess = fight(вожак={}, громила={})
    styled(sess, "вожак", "leader")
    styled(sess, "громила", "brute")
    decision = engine.choose_enemy_action(sess, "вожак")
    assert decision["kind"] == "command" and decision["mark_user_id"] == 2
    sess.local_enemy_rules["вожак"]["mark"] = 2
    # The brute would normally hit the weakest hero (1) but follows the order.
    assert engine.choose_enemy_action(sess, "громила")["target_user_id"] == 2
    sess.enemy_combatants["громила"].update(hp=0, status="dead")
    assert engine.choose_enemy_action(sess, "вожак")["kind"] == "attack"
    sess.enemy_combatants["вожак"].update(hp=0, status="dead")
    sess.enemy_combatants["громила"].update(hp=20, status="alive")
    assert engine.choose_enemy_action(sess, "громила")["target_user_id"] == 1  # dead leader's mark is void


def test_skirmisher_alternates_attack_and_reposition_through_real_rounds():
    sess = fight(ловкач={})
    styled(sess, "ловкач", "skirmisher")
    combat, _calls = backend()
    combat.combat._resolve_enemy_attack = lambda sess, attack: (f"👹 {attack['reason']} → {attack['target_user_id']}", "", False)
    texts = []
    for number in range(1, 4):
        lifecycle.start_combat_round(sess)
        for uid in (1, 2):
            lifecycle.note_actor_resolved(sess, uid)
        result = engine.execute_enemy_action(sess, "ловкач", operation_id=f"op-{number}", backend=combat)
        texts.append(result.text)
    assert "отскакивает" not in texts[0] and "отскакивает" in texts[1] and "отскакивает" not in texts[2]


def test_leader_command_is_recorded_in_rules_and_is_not_a_meaningful_event():
    sess = fight(вожак={}, громила={})
    styled(sess, "вожак", "leader")
    styled(sess, "громила", "brute")
    combat, _calls = backend()
    lifecycle.start_combat_round(sess)
    for uid in (1, 2):
        lifecycle.note_actor_resolved(sess, uid)
    result = engine.execute_enemy_action(sess, "вожак", operation_id="op-lead", backend=combat)
    assert "Боря" in result.text and sess.local_enemy_rules["вожак"]["mark"] == 2
    assert result.needs_narrator is False
