import copy
import json
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_conditions as conditions
from AI import dnd_inventory_fun as inventory
from AI import dnd_item_actions as items
from AI import dnd_player_combat as player_combat
from AI import dnd_special_moves as special
from AI import dnd_cinematic_combat as cinematic
from AI import dnd_local_engine as engine
from AI import dnd_scene_rules as scene_rules
from AI import dnd_turn_lifecycle as lifecycle


class Dice:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = 0

    def randint(self, low, high):
        self.calls += 1
        return next(self.values)


def session():
    result = SimpleNamespace(campaign_id="test-campaign", state_revision=0, state="WAITING_ACTION", mode="participants",
        participants={"1": {"user_id": 1, "name": "Алиса", "active": True}, "2": {"user_id": 2, "name": "Боря", "active": True}},
        character_profiles={"1": {"special": "рывок"}},
        character_sheets={str(uid): {"hp": 14, "max_hp": 14, "ac": 13, "status": "alive", "stats": {"STR": 16, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8}} for uid in [1, 2]},
        inventories={"1": [{"name": "меч", "quantity": 1}, {"name": "ключ", "quantity": 2}], "2": []},
        conditions={}, player_positions={"1": {"location": "площадь"}, "2": {"location": "площадь"}},
        pending_generation_request={}, pending_generated_result={}, pending_roll=None,
        enemy_combatants={"огр": {"name": "Огр", "power": "HIGH", "hp": 28, "max_hp": 28, "ac": 14, "status": "alive"}},
        action_prompt_message_id=10, action_target_user_ids=[1], pending_actions={}, action_deadline=None, scene_count=3,
        scene_clocks={"alarm": {"id": "alarm", "name": "Тревога", "value": 0, "max": 4, "full": False, "when_full": "стража прибыла", "history": []}},
        scene_objects={"cart": {"id": "cart", "name": "Телега", "available": True, "state": "в проходе"}},
        special_move_charges={"1": 1}, special_move_pending_user_id=None, special_move_offer_user_id=None,
        paused=False)
    engine.ensure(result)
    lifecycle.sync_window(result)
    return result


def backend(*values):
    calls = []

    def commit(sess, pending, actor):
        calls.append((pending["type"], actor))
        if sess.special_move_pending_user_id == actor:
            special._commit_completed_special_roll(sess, pending, actor)
        conditions._consume_pending_uses(sess, pending)
        return []

    combat = SimpleNamespace(ability_modifier=lambda n: (n - 10) // 2,
                             _participant_name=lambda s, uid: s.participants[str(uid)]["name"])
    return engine.EngineBackend(SimpleNamespace(_commit_roll_transaction=commit), combat, player_combat, cinematic, inventory, items, conditions, Dice(*values)), calls


def action(sess, kind="ATTACK", *, actor=1, operation="operation-1", **kwargs):
    return engine.make_action(sess, actor, kind, operation_id=operation, **kwargs)


def test_roll_phase_preserves_group_window_and_navigation_has_no_tick():
    sess = session()
    sess.action_target_user_ids = []
    win = lifecycle.sync_window(sess)
    original = copy.deepcopy(sess.dnd_lifecycle_v1)
    for _ in range(3):
        assert lifecycle.current_window(sess)["turn_id"] == win["turn_id"]
    assert sess.dnd_lifecycle_v1 == original
    sess.pending_roll = {"type": "CHECK", "target_user_ids": [2]}
    sess.state = "WAITING_ROLL"
    roll = lifecycle.sync_window(sess)
    assert roll["turn_id"] == win["turn_id"]
    assert roll["actors"] == [1, 2]
    assert roll["roll_actors"] == [2]
    assert roll["phase"] == "roll"


def test_missing_prompt_then_telegram_id_does_not_create_another_turn():
    sess = session()
    lifecycle.close_window(sess)
    sess.action_prompt_message_id = None
    first = lifecycle.sync_window(sess)
    sess.action_prompt_message_id = 42
    second = lifecycle.sync_window(sess)
    assert first["turn_id"] == second["turn_id"]
    assert second["source_id"] == "42"


@pytest.mark.parametrize("override", [dict(actor_id=2), dict(campaign_id="foreign"), dict(turn_id="old"), dict(phase="roll"), dict(expected_revision=5)])
def test_foreign_or_stale_callback_cannot_mutate_or_roll(override):
    sess = session()
    before = copy.deepcopy(vars(sess))
    be, calls = backend(19)
    act = action(sess, target_id="огр", item_id="меч")
    act = engine.Action(**(vars(act) | override))
    with pytest.raises(engine.LocalActionError):
        engine.execute_action(sess, act, backend=be)
    assert vars(sess) == before
    assert be.rng.calls == 0
    assert not calls


@pytest.mark.parametrize("guard", ["paused", "pending_generated_result", "pending_generation_request"])
def test_pending_recovery_and_pause_guard(guard):
    sess = session()
    setattr(sess, guard, True if guard == "paused" else {"id": "in-progress"})
    be, _ = backend(12)
    with pytest.raises(engine.LocalActionError):
        engine.execute_action(sess, action(sess, target_id="огр", item_id="меч"), backend=be)
    assert be.rng.calls == 0


def test_attack_miss_is_journaled_commits_conditions_and_special_and_replays():
    sess = session()
    sess.state = "WAITING_ROLL"
    sess.pending_roll = {"type": "ATTACK", "mode": "DISADVANTAGE", "target_user_ids": [1], "attack": {"enemy_key": "огр", "weapon": "меч", "style": "MELEE"}}
    conditions._add(sess, {"PLAYER": "1", "NAME": "шок", "EFFECT": "NEXT_ROLL_DISADVANTAGE", "USES": "1"})
    conditions._queue_pending_uses(sess, [("1", "NEXT_ROLL_DISADVANTAGE")])
    sess.special_move_offer_user_id = 1
    assert special.activate_special(sess, 1)[0]
    lifecycle.sync_window(sess)
    be, calls = backend(7)
    act = action(sess, target_id="огр")
    result = engine.execute_action(sess, act, backend=be, ai_mode="economy")
    assert sess.enemy_combatants["огр"]["hp"] == 28
    assert sess.special_move_charges["1"] == 0
    assert sess.conditions["1"] == []
    assert calls == [("ATTACK", 1)]
    assert result.events[0]["type"] == "AttackResolved"
    assert not result.needs_narrator
    assert len(sess.action_records) == 1
    assert "промах" in engine.gameplay_journal(sess)[0]
    retry = engine.execute_action(sess, act, backend=be)
    assert retry.replayed and retry.text == result.text
    assert be.rng.calls == 1 and calls == [("ATTACK", 1)]
    restored = session()
    engine.restore(restored, {engine.STATE_FIELD: engine.state_field(sess)})
    assert engine.execute_action(restored, act, backend=be).replayed
    assert be.rng.calls == 1


def test_operation_token_cannot_be_reused_with_new_input():
    sess = session()
    be, _ = backend(7)
    act = action(sess, target_id="огр", item_id="меч")
    engine.execute_action(sess, act, backend=be)
    altered = engine.Action(**(vars(act) | {"item_id": "ключ"}))
    with pytest.raises(engine.LocalActionError):
        engine.execute_action(sess, altered, backend=be)


def test_group_window_stays_open_for_other_actor_and_round_is_optional():
    sess = session()
    sess.action_target_user_ids = []
    lifecycle.sync_window(sess)
    be, _ = backend()
    first = engine.execute_action(sess, action(sess, "TRANSFER_ITEM", target_id=2, item_id="ключ", inputs={"quantity": 1}), backend=be)
    assert not first.window_closed and not first.round_complete
    assert lifecycle.current_window(sess)["kind"] == "group"
    assert sess.state == "WAITING_ACTION"
    with pytest.raises(engine.LocalActionError):
        engine.execute_action(sess, action(sess, "WAIT", operation="extra"), backend=be)
    second = engine.execute_action(sess, action(sess, "WAIT", actor=2, operation="second"), backend=be)
    assert second.window_closed and not second.round_complete


def test_explicit_round_completion_only_after_real_actions():
    sess = session()
    lifecycle.start_combat_round(sess)
    be, _ = backend()
    first = engine.execute_action(sess, action(sess, "WAIT"), backend=be)
    assert not first.round_complete
    lifecycle.begin_window(sess, kind="personal", actors=[2])
    sess.state = "WAITING_ACTION"
    second = engine.execute_action(sess, action(sess, "WAIT", actor=2, operation="second"), backend=be)
    assert second.round_complete and second.needs_narrator


def test_item_reference_uses_full_saved_content_and_is_rechecked():
    sess = session()
    be, _ = backend()
    ref = engine.item_reference(sess.inventories["1"][1], 1)
    result = engine.execute_action(sess, action(sess, "TRANSFER_ITEM", target_id=2, item_id=ref["item_id"], inputs=ref | {"quantity": 1}), backend=be)
    assert "ключ" in result.text
    sess = session()
    sess.inventories["1"][1]["quantity"] = 1
    with pytest.raises(engine.LocalActionError):
        engine.execute_action(sess, action(sess, "TRANSFER_ITEM", target_id=2, item_id=ref["item_id"], inputs=ref), backend=be)


def test_dead_absent_or_remote_recipient_cannot_receive():
    be, _ = backend()
    for attribute in ["dead", "absent", "remote"]:
        sess = session()
        if attribute == "dead":
            sess.character_sheets["2"]["hp"] = 0
        elif attribute == "absent":
            sess.participants["2"]["active"] = False
        else:
            sess.player_positions["2"]["location"] = "крыша"
        with pytest.raises(engine.LocalActionError):
            engine.execute_action(sess, action(sess, "TRANSFER_ITEM", target_id=2, item_id="ключ"), backend=be)


def rule():
    return {"label": "Толкнуть телегу", "uncertain": True, "ability": "STR", "dc": 12, "failure_price": True, "once": True,
            "success": {"text": "Проход открыт.", "effects": [{"kind": "object", "object_id": "cart", "state": "у стены"}]},
            "failure": {"text": "Стража услышала шум.", "effects": [{"kind": "clock", "clock_id": "alarm", "delta": 1}]}}


def test_object_rule_declares_failure_and_clock_changes_once():
    sess = session()
    sess.scene_objects["cart"]["interactions"] = {"push": rule()}
    be, calls = backend(4)
    act = action(sess, "OBJECT", object_id="cart", inputs={"rule_id": "push"})
    result = engine.execute_action(sess, act, backend=be)
    assert sess.scene_clocks["alarm"]["value"] == 1
    assert "шум" in result.text
    assert calls == [("CHECK", 1)]
    assert len({event["event_id"] for event in result.events}) == len(result.events)
    engine.execute_action(sess, act, backend=be)
    assert sess.scene_clocks["alarm"]["value"] == 1 and be.rng.calls == 1
    lifecycle.begin_window(sess, kind="personal", actors=[1])
    sess.state = "WAITING_ACTION"
    with pytest.raises(engine.LocalActionError):
        engine.execute_action(sess, action(sess, "OBJECT", operation="try-again", object_id="cart", inputs={"rule_id": "push"}), backend=be)


def test_metadata_update_is_atomic_and_strips_hidden_service_block():
    sess = session()
    broken = rule()
    broken["failure"]["effects"][0]["clock_id"] = "unknown"
    block = "<DND_RULES>" + json.dumps({"cart": {"good": rule(), "bad": broken}}) + "</DND_RULES>"
    text, notices = scene_rules.apply_scene_rule_metadata(sess, block, "Описание.\n" + block, [])
    assert text == "Описание." and not notices
    assert "interactions" not in sess.scene_objects["cart"]
    block = "<DND_RULES>" + json.dumps({"cart": {"push": rule()}}) + "</DND_RULES>"
    scene_rules.apply_scene_rule_metadata(sess, block, block, [])
    assert "push" in sess.scene_objects["cart"]["interactions"]


def test_failed_reducer_does_not_leave_partial_inventory_or_effects():
    sess = session()
    contract = rule()
    contract["success"]["effects"] = [{"kind": "clock", "clock_id": "alarm", "delta": 1}, {"kind": "item", "operation": "remove", "name": "нет такого", "quantity": 1}]
    sess.scene_objects["cart"]["interactions"] = {"push": contract}
    before = copy.deepcopy(vars(sess))
    be, _ = backend(19)
    with pytest.raises(engine.LocalActionError):
        engine.execute_action(sess, action(sess, "OBJECT", object_id="cart", inputs={"rule_id": "push"}), backend=be)
    assert vars(sess) == before


def test_action_record_prestate_has_no_recursive_ledger_or_menu():
    sess = session()
    sess.menu_ui = {"large": list(range(100))}
    be, _ = backend()
    for index in range(5):
        engine.execute_action(sess, action(sess, "WAIT", operation=str(index)), backend=be)
        lifecycle.begin_window(sess, kind="personal", actors=[1])
        sess.state = "WAITING_ACTION"
    for record in sess.action_records:
        assert engine.STATE_FIELD not in record["pre_state"]
        assert "action_records" not in record["pre_state"]
        assert "menu_ui" not in record["pre_state"]
    assert len(json.dumps(engine.state_field(sess))) < 50000


def test_expiry_is_explicit_and_legacy_narration_policy_is_preserved():
    sess = session()
    sess.conditions = {"1": [{"name": "old", "scenes_remaining": 2}, {"name": "new", "expiry": {"version": 1, "tick": "action", "actor_id": 1, "remaining": 1}}]}
    assert lifecycle.tick_event(sess, "action", "operation-two", actor_id=2)
    assert len(sess.conditions["1"]) == 2
    assert lifecycle.tick_event(sess, "action", "operation-one", actor_id=1)
    assert sess.conditions["1"] == [{"name": "old", "scenes_remaining": 2}]
    assert not lifecycle.tick_event(sess, "action", "operation-one", actor_id=1)
    assert sess.dnd_lifecycle_v1["legacy_condition_policy"] == "narration"


def test_simple_enemy_policy_has_reaction_guard_and_no_attack_on_absence():
    sess = session()
    sess.local_enemy_rules = {"огр": {"policy": "simple"}}
    assert engine.choose_enemy_action(sess, "огр") is None
    lifecycle.close_window(sess)
    sess.enemy_intent = {"due": False}
    assert engine.choose_enemy_action(sess, "огр") is None
    sess.enemy_intent["due"] = True
    assert engine.choose_enemy_action(sess, "огр")["kind"] == "attack"
    for player in sess.participants.values():
        player["active"] = False
    assert engine.choose_enemy_action(sess, "огр") is None


@pytest.mark.parametrize("field,value", [("uncertain", "false"), ("public", "false"), ("once", 1), ("failure_price", "yes")])
def test_scene_rules_require_json_booleans(field, value):
    from AI.dnd_scene_rules import validate_rule, SceneRuleError

    rule = {"label": "Осмотреть", "uncertain": False, "success": {"text": "Пусто."}, field: value}
    with pytest.raises(SceneRuleError):
        validate_rule(rule)
