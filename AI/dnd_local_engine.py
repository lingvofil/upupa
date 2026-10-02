"""Synchronous, transactional known actions; no provider or Telegram calls.

Public integration contract:
  configure_state_policy(policy)
  backend = EngineBackend.from_runtime(dnd)
  action = make_action(session, actor_id, kind, operation_id=server_token, ...)
  result = execute_action(session, action, backend=backend)
  persist_dnd_sessions() BEFORE delivering result.text or scheduling narration.

ActionResult records include the exact committed dice/result. Retried operations
return that result, even after their window has closed, without repeating hooks.
The existing durable transport/outbox remains responsible for message delivery.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

from AI import dnd_scene_rules as rules
from AI import dnd_turn_lifecycle as lifecycle


STATE_FIELD = "dnd_local_engine_v1"
RECORD_LIMIT = 100
KNOWN_KINDS = {"ROLL", "ATTACK", "CINEMATIC_ATTACK", "USE_ITEM", "TRANSFER_ITEM", "OBJECT", "WAIT"}
_TRANSIENT_ATTRIBUTES = {"chat_session", "conversation", "pending_generated_result", "pending_generation_request", "event_journal"}
_PRESTATE_FIELDS = {
    "state", "state_revision", "campaign_id", "participants", "character_sheets", "inventories", "conditions",
    "player_positions", "enemy_combatants", "scene_clocks", "scene_objects", "enemy_intent", "pending_roll",
    "action_target_user_ids", "pending_actions", "action_prompt_message_id", "action_deadline", "healing_charge",
    "healing_charges", "special_move_charges", "special_move_pending_user_id", "special_move_offer_user_id",
    "luck_tokens", "weakness_luck_earned", "weakness_luck_spent", "item_boosts", "item_world_facts",
    "local_object_uses", "local_world_facts", "local_enemy_rules", "local_wait_rule", lifecycle.STATE_FIELD,
}


class LocalActionError(ValueError):
    """An action is stale, unauthorized, unknown, or mechanically invalid."""


@dataclass(frozen=True)
class Action:
    operation_id: str
    campaign_id: str
    turn_id: str
    phase: str
    expected_revision: int
    actor_id: int
    kind: str
    target_id: str | int | None = None
    item_id: str | None = None
    object_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def opid(self):
        return self.operation_id


@dataclass
class ActionResult:
    operation_id: str
    text: str
    events: list[dict]
    dice: list[dict] = field(default_factory=list)
    continuation_prompt: str = ""
    needs_narrator: bool = False
    round_complete: bool = False
    window_closed: bool = False
    next_actor_id: int | None = None
    replayed: bool = False
    status: str = "committed"


@dataclass
class EngineBackend:
    """Explicit code-only dependencies, also injectable for offline tests."""
    dnd: Any = None
    combat: Any = None
    player_combat: Any = None
    cinematic: Any = None
    inventory: Any = None
    items: Any = None
    conditions: Any = None
    rng: Any = None

    @classmethod
    def from_runtime(cls, dnd):
        from AI import dnd_combat, dnd_player_combat, dnd_cinematic_combat
        from AI import dnd_inventory_fun, dnd_item_actions, dnd_conditions

        return cls(dnd, dnd_combat, dnd_player_combat, dnd_cinematic_combat,
                   dnd_inventory_fun, dnd_item_actions, dnd_conditions)


def ensure(session) -> dict:
    raw = getattr(session, STATE_FIELD, None)
    row = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    row["version"] = 1
    row["action_records"] = [record for record in (row.get("action_records") or []) if isinstance(record, dict)][-RECORD_LIMIT:]
    results = row.get("operation_results") or {}
    order = [str(v) for v in (row.get("operation_order") or []) if str(v) in results][-RECORD_LIMIT:]
    row["operation_order"] = order
    row["operation_results"] = {key: results[key] for key in order}
    # A bounded result cache alone must never make old actions executable again.
    # Closed turn IDs and stale phase/revision checks reject evicted callbacks.
    row.setdefault("scheduler", {"cursor": 0, "enemy_acted_round": {}})
    if not isinstance(getattr(session, "local_object_uses", None), dict):
        session.local_object_uses = copy.deepcopy(row.get("local_object_uses") or {})
    if not isinstance(getattr(session, "local_world_facts", None), list):
        session.local_world_facts = copy.deepcopy(row.get("local_world_facts") or [])
    for name in ("local_enemy_rules", "local_wait_rule"):
        if not hasattr(session, name):
            setattr(session, name, copy.deepcopy(row.get(name) or {}))
    setattr(session, STATE_FIELD, row)
    session.action_records = row["action_records"]
    return row


def state_field(session) -> dict:
    row = copy.deepcopy(ensure(session))
    row["local_object_uses"] = copy.deepcopy(session.local_object_uses)
    row["local_world_facts"] = copy.deepcopy(session.local_world_facts)
    row["local_enemy_rules"] = copy.deepcopy(session.local_enemy_rules)
    row["local_wait_rule"] = copy.deepcopy(session.local_wait_rule)
    return row


def restore(session, data) -> None:
    row = copy.deepcopy((data or {}).get(STATE_FIELD) or {}) if isinstance(data, dict) else {}
    setattr(session, STATE_FIELD, row)
    session.local_object_uses = copy.deepcopy(row.get("local_object_uses") or {})
    session.local_world_facts = copy.deepcopy(row.get("local_world_facts") or [])
    session.local_enemy_rules = copy.deepcopy(row.get("local_enemy_rules") or {})
    session.local_wait_rule = copy.deepcopy(row.get("local_wait_rule") or {})
    ensure(session)


def configure_state_policy(policy) -> None:
    policy.add_ensure_hook(lifecycle.ensure)
    policy.add_state_field(lifecycle.STATE_FIELD, lifecycle.state_field)
    policy.add_restore_hook(lifecycle.restore)
    policy.add_ensure_hook(ensure)
    policy.add_state_field(STATE_FIELD, state_field)
    policy.add_restore_hook(restore)


def make_action(session, actor_id: int, kind: str, *, operation_id: str, **kwargs) -> Action:
    window = lifecycle.current_window(session)
    if not window or window.get("status") != "open":
        raise LocalActionError("Сейчас нет открытого хода.")
    return Action(str(operation_id), str(getattr(session, "campaign_id", "") or ""),
                  window["turn_id"], window["phase"], int(getattr(session, "state_revision", 0) or 0),
                  int(actor_id), str(kind).upper(), **kwargs)


def _fingerprint(action: Action) -> str:
    return hashlib.sha256(json.dumps(dataclasses.asdict(action), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def item_reference(item, index: int) -> dict:
    """Stable reference for legacy dictionaries without assigning live IDs."""
    fingerprint = hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"item_id": str(item.get("id") or "legacy:" + fingerprint[:20]) if isinstance(item, dict) else "legacy:" + fingerprint[:20],
            "item_name": str(item.get("name") or "") if isinstance(item, dict) else str(item),
            "item_index": index, "item_fingerprint": fingerprint}


def _owned_item(session, actor_id: int, item_id, selectors=None) -> tuple[int, Any]:
    inventory = (getattr(session, "inventories", {}) or {}).get(str(actor_id), [])
    selectors = selectors or {}
    if str(item_id or "").startswith("legacy:"):
        candidates = []
        for index, item in enumerate(inventory):
            ref = item_reference(item, index)
            # Accept the same full-item hash with standard JSON separators from
            # older menus as well; neither spelling bypasses content validation.
            alternate = hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if item_id not in {ref["item_id"], "legacy:" + alternate[:20]}:
                continue
            if selectors.get("item_index") is not None and selectors["item_index"] != index:
                continue
            fingerprint = selectors.get("item_fingerprint")
            if fingerprint is not None and str(fingerprint) not in {ref["item_fingerprint"], alternate, ref["item_fingerprint"][:20], alternate[:20]}:
                continue
            candidates.append((index, item))
        if len(candidates) == 1:
            return candidates[0]
        raise LocalActionError("Предмет изменился или выбран неоднозначно. Обнови меню.")
    candidates = []
    for index, item in enumerate(inventory):
        if isinstance(item, dict):
            identifiers = {str(item.get("id") or ""), str(item.get("name") or "")}
        else:
            identifiers = {str(item)}
        if item_id is not None and str(item_id) in identifiers - {""}:
            candidates.append((index, item))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise LocalActionError("Выбери конкретный экземпляр предмета.")
    raise LocalActionError("Этого предмета нет в твоём инвентаре.")


def validate_action(session, action: Action) -> None:
    if not isinstance(action, Action) or not action.operation_id or len(action.operation_id) > 160:
        raise LocalActionError("Неизвестная операция.")
    if action.kind not in KNOWN_KINDS:
        raise LocalActionError("Для этой свободной идеи нужен мастер.")
    if action.campaign_id != str(getattr(session, "campaign_id", "") or ""):
        raise LocalActionError("Эта кнопка относится к другой партии.")
    if getattr(session, "pending_generation_request", None) or getattr(session, "pending_generated_result", None):
        raise LocalActionError("Сначала восстанови сохранённое продолжение.")
    if getattr(session, "paused", False):
        raise LocalActionError("Партия на паузе.")
    window = lifecycle.current_window(session)
    if not window or window["status"] != "open" or action.turn_id != window["turn_id"] or action.phase != window["phase"]:
        raise LocalActionError("Этот ход уже закрыт или перешёл к другой фазе.")
    if int(action.expected_revision) != int(getattr(session, "state_revision", 0) or 0):
        raise LocalActionError("Состояние изменилось. Обнови меню.")
    if getattr(session, "state", "") not in {"WAITING_ACTION", "WAITING_ROLL"}:
        raise LocalActionError("Сейчас нельзя выполнить это действие.")
    if int(action.actor_id) not in lifecycle.active_actor_ids(session):
        raise LocalActionError("Выполнить действие может живой участник сцены.")
    if window.get("actors") and int(action.actor_id) not in window["actors"]:
        raise LocalActionError("Сейчас ход другого игрока.")
    answered = window.get("resolved_actors") or []
    if int(action.actor_id) in answered:
        raise LocalActionError("Твоё действие в этом окне уже разрешено.")
    if action.phase == "roll":
        pending = getattr(session, "pending_roll", None) or {}
        targets = [int(uid) for uid in pending.get("target_user_ids", [])]
        if targets and action.actor_id not in targets:
            raise LocalActionError("Этот бросок не твой.")
        if action.kind not in {"ROLL", "ATTACK", "CINEMATIC_ATTACK"}:
            raise LocalActionError("Сейчас нужно разрешить сохранённый бросок.")
    elif action.kind == "ROLL":
        raise LocalActionError("Нет сохранённой проверки.")
    if action.kind in {"USE_ITEM", "TRANSFER_ITEM"}:
        _owned_item(session, action.actor_id, action.item_id, action.inputs)
    if action.kind == "TRANSFER_ITEM":
        try:
            target = int(action.target_id)
        except (TypeError, ValueError):
            raise LocalActionError("Укажи получателя из партии.") from None
        if target == action.actor_id or target not in lifecycle.active_actor_ids(session):
            raise LocalActionError("Передать вещь можно другому живому участнику сцены.")
        locations = getattr(session, "player_positions", {}) or {}
        here = (locations.get(str(action.actor_id)) or {}).get("location")
        there = (locations.get(str(target)) or {}).get("location")
        if here and there and here != there:
            raise LocalActionError("Для передачи сначала окажитесь в одном месте.")
    if action.kind == "OBJECT":
        try:
            rule = rules.get_interaction(session, str(action.object_id or ""), str(action.inputs.get("rule_id") or ""))
            if not rule.get("public", True):
                raise LocalActionError("Это взаимодействие ещё не раскрыто.")
            rules.validate_targets(session, action.actor_id, rule)
        except rules.SceneRuleError as exc:
            raise LocalActionError(str(exc)) from exc
    if action.kind in {"ATTACK", "CINEMATIC_ATTACK"}:
        _attack_pending(session, action)


def _clone(session):
    draft = copy.copy(session)
    # Client objects are never deep-copied. Only JSON-shaped mechanical fields
    # can enter a reducer, its durable pre-state, or an action record.
    names = set()
    for name, value in vars(session).items():
        if name in _TRANSIENT_ATTRIBUTES or name.startswith("_upupa") or name == "chat_session":
            continue
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        setattr(draft, name, copy.deepcopy(value))
        names.add(name)
    return draft, names


def _snapshot(session, names):
    return {name: copy.deepcopy(getattr(session, name)) for name in sorted(set(names) & _PRESTATE_FIELDS) if hasattr(session, name)}


def _dice(backend, mode):
    rng = backend.rng or random.SystemRandom()
    first = rng.randint(1, 20)
    mode = str(mode or "NORMAL").upper()
    if mode in {"ADVANTAGE", "DISADVANTAGE"}:
        second = rng.randint(1, 20)
        return [first, second], max(first, second) if mode == "ADVANTAGE" else min(first, second)
    return [first], first


def _attack_pending(session, action):
    existing = getattr(session, "pending_roll", None) or {}
    if action.phase == "roll":
        wanted = "CINEMATIC_ATTACK" if action.kind == "CINEMATIC_ATTACK" else "ATTACK"
        if existing.get("type") != wanted:
            raise LocalActionError("Тип сохранённого броска изменился.")
        pending = copy.deepcopy(existing)
        attack = pending.get("cinematic" if wanted == "CINEMATIC_ATTACK" else "attack") or {}
        if action.target_id is not None and str(action.target_id) != str(attack.get("enemy_key")):
            raise LocalActionError("Цель сохранённой атаки изменена.")
    else:
        if action.kind == "CINEMATIC_ATTACK":
            raise LocalActionError("Нестандартный смертельный трюк должен быть заранее подтверждён мастером.")
        attack = {"enemy_key": str(action.target_id or ""), "weapon": str(action.item_id or "без оружия"), "style": "MELEE"}
        pending = {"type": "ATTACK", "target_user_ids": [action.actor_id], "mode": "NORMAL", "attack": attack}
    enemy = (getattr(session, "enemy_combatants", {}) or {}).get(str(attack.get("enemy_key")))
    if not isinstance(enemy, dict) or enemy.get("status") == "dead" or int(enemy.get("hp", 0)) <= 0:
        raise LocalActionError("Живая цель атаки не найдена.")
    if ((getattr(session, "local_enemy_rules", {}) or {}).get(str(attack.get("enemy_key"))) or {}).get("retreated"):
        raise LocalActionError("Противник отступил. Для преследования опиши новый ход мастеру.")
    weapon = str(attack.get("weapon") or "без оружия")
    if action.kind != "CINEMATIC_ATTACK" and weapon.casefold() not in {"без оружия", "unarmed", "кулак", "голыми руками", "пинок"}:
        _, owned = _owned_item(session, action.actor_id, action.item_id or weapon, action.inputs)
        # Caller may select only inventory ID; mechanics uses the saved name.
        owned_name = str(owned.get("name") if isinstance(owned, dict) else owned)
        if action.phase == "roll" and owned_name != weapon:
            raise LocalActionError("Оружие сохранённой атаки нельзя заменить старой кнопкой.")
        attack["weapon"] = owned_name
        attack["style"] = str(owned.get("attack_style") or attack.get("style") or "MELEE") if isinstance(owned, dict) else str(attack.get("style") or "MELEE")
    location = ((getattr(session, "player_positions", {}) or {}).get(str(action.actor_id)) or {}).get("location")
    allowed = enemy.get("reachable_from")
    if allowed and location not in allowed:
        raise LocalActionError("Противник недоступен из сохранённой позиции.")
    if action.kind == "CINEMATIC_ATTACK":
        object_id = attack.get("tactical_scene_object_id")
        obj = (getattr(session, "scene_objects", {}) or {}).get(str(object_id))
        if not object_id or not isinstance(obj, dict) or not obj.get("available", True):
            raise LocalActionError("Объект для трюка уже недоступен.")
    return pending


def _roll_commit(backend, draft, pending, actor_id):
    draft.pending_roll = None
    draft.state = "RESOLVING"
    if backend.dnd is None or not hasattr(backend.dnd, "_commit_roll_transaction"):
        raise LocalActionError("Транзакция расхода броска не подключена.")
    return backend.dnd._commit_roll_transaction(draft, pending, actor_id) or []


def _resolve(draft, action, backend):
    text, prompt, events, dice = "", "", [], []
    if action.kind == "ROLL":
        pending = copy.deepcopy(getattr(draft, "pending_roll", None) or {})
        if pending.get("type") in {"ATTACK", "CINEMATIC_ATTACK"}:
            raise LocalActionError("Используй сохранённый тип атаки.")
        ability = str(pending.get("ability") or "").upper()
        if ability not in rules.ABILITIES and backend.combat is not None and hasattr(backend.combat, "_ability_for_roll"):
            ability = str(backend.combat._ability_for_roll(pending) or "").upper()
        dc = pending.get("dc")
        if ability not in rules.ABILITIES or not isinstance(dc, int) or isinstance(dc, bool):
            raise LocalActionError("У проверки нет известных характеристики и сложности.")
        # A model-adjudicated check without authored consequences still needs
        # a narrator after showing its exact local result.
        values, natural = _dice(backend, pending.get("mode"))
        stats = ((getattr(draft, "character_sheets", {}) or {}).get(str(action.actor_id)) or {}).get("stats") or {}
        modifier = (int(stats.get(ability, 10)) - 10) // 2
        total = natural + modifier
        success = total >= dc
        dice.append({"kind": "d20", "values": values, "selected": natural})
        events.append({"type": "RollResolved", "actor_id": action.actor_id, "ability": ability, "dc": dc, "total": total, "success": success})
        notices = _roll_commit(backend, draft, pending, action.actor_id)
        text = f"🎲 {ability}: {natural} {modifier:+d} = {total} против {dc} — {'успех' if success else 'провал'}."
        if notices:
            text += "\n" + "\n".join(notices)
        contract = pending.get("local_rule")
        if isinstance(contract, dict):
            rule = rules.validate_rule(contract)
            rules.validate_targets(draft, action.actor_id, rule)
            branch = rules.select_branch(rule, total)
            text += "\n" + rule[branch]["text"]
            events += rules.apply_effects(draft, action.actor_id, rule[branch].get("effects", []), source_id=action.operation_id, backend=backend)
        else:
            prompt = f"Подтверждённый результат проверки: {pending.get('reason') or 'заявленное действие'}. {text} Разреши конкретные последствия, не меняя кубик и исход."
    elif action.kind in {"ATTACK", "CINEMATIC_ATTACK"}:
        pending = _attack_pending(draft, action)
        if action.phase != "roll" and backend.conditions is not None:
            tag = f"[ACTION:PLAYER_ATTACK;TARGETS:{action.actor_id};MODE:NORMAL]"
            adjusted, consumed = backend.conditions.apply_condition_penalties(draft, tag)
            pending["mode"] = "DISADVANTAGE" if "MODE:DISADVANTAGE" in adjusted else "NORMAL"
            draft.pending_roll = pending
            backend.conditions._queue_pending_uses(draft, consumed)
            pending = draft.pending_roll
        values, natural = _dice(backend, pending.get("mode"))
        dice.append({"kind": "d20", "values": values, "selected": natural})
        enemy_key = (pending.get("cinematic") or pending.get("attack") or {}).get("enemy_key")
        before = copy.deepcopy(draft.enemy_combatants[str(enemy_key)])
        if action.kind == "ATTACK":
            text, prompt = backend.player_combat._resolve_attack_mechanics(backend.combat, draft, action.actor_id, pending, natural)
        else:
            text, prompt = backend.cinematic._resolve_cinematic_mechanics(backend.combat, backend.player_combat, draft, action.actor_id, pending, natural)
        notices = _roll_commit(backend, draft, pending, action.actor_id)
        if notices:
            text += "\n" + "\n".join(notices)
        after = copy.deepcopy(draft.enemy_combatants[str(enemy_key)])
        events.append({"type": "AttackResolved", "actor_id": action.actor_id, "enemy_id": str(enemy_key), "before_hp": before["hp"], "after_hp": after["hp"], "natural": natural, "meaningful": before.get("status") != after.get("status"), "result_text": text})
        # Damage mechanics already records the exact rolled dice in its summary;
        # store that immutable detail alongside HP, so replay never re-randomizes.
        dice.append({"kind": "damage", "amount": int(before["hp"]) - int(after["hp"]), "detail": text})
        if not events[-1]["meaningful"] and action.kind == "ATTACK":
            prompt = ""
    elif action.kind == "TRANSFER_ITEM":
        _, item = _owned_item(draft, action.actor_id, action.item_id, action.inputs)
        name = str(item.get("name") if isinstance(item, dict) else item)
        quantity = action.inputs.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise LocalActionError("Укажи целое положительное количество.")
        moved, _ = backend.inventory.transfer_between_inventories(draft.inventories, action.actor_id, int(action.target_id), name, quantity)
        text = f"🎒 Передано: {moved}."
        events.append({"type": "ItemTransferred", "actor_id": action.actor_id, "target_id": int(action.target_id), "name": name, "quantity": quantity})
    elif action.kind == "USE_ITEM":
        index, item = _owned_item(draft, action.actor_id, action.item_id, action.inputs)
        if not isinstance(item, dict) or not backend.items._mechanic(item):
            raise LocalActionError("У этого предмета пока нет известных активных правил.")
        plan = {"index": index, "name": item["name"], "mechanic": backend.items._mechanic(item), "requirement": backend.items._requirement(item), "cost": backend.items._cost(item), "tail": str(action.inputs.get("declaration") or "")}
        ok, error, plan = backend.items._prevalidate_plan(draft, action.actor_id, plan)
        if not ok:
            raise LocalActionError(error or "Предмет недоступен.")
        before = int(backend.items._charges_remaining(item))
        notices = backend.items._commit_plan(draft, action.actor_id, plan)
        if not notices:
            raise LocalActionError("Не удалось применить известный эффект предмета.")
        text = "\n".join(notices)
        events.append({"type": "ItemUsed", "actor_id": action.actor_id, "item_id": action.item_id, "before_charges": before, "meaningful": backend.items._mechanic(item) in {"CREATE_EXIT", "CLEAR_ACCUSATION", "CLOCK_COMPLETE"}})
    elif action.kind == "OBJECT":
        object_id, rule_id = str(action.object_id), str(action.inputs.get("rule_id") or "")
        rule = rules.get_interaction(draft, object_id, rule_id)
        rules.validate_targets(draft, action.actor_id, rule)
        total = None
        if rule["uncertain"]:
            # A declared object check has known effects on both outcomes and can
            # therefore resolve in one operation, without a second AI step.
            stats = ((getattr(draft, "character_sheets", {}) or {}).get(str(action.actor_id)) or {}).get("stats") or {}
            pending = {"type": "CHECK", "ability": rule["ability"], "dc": rule["dc"], "target_user_ids": [action.actor_id], "mode": "NORMAL"}
            if backend.conditions is not None:
                tag = f"[ACTION:ROLL;TARGETS:{action.actor_id};MODE:NORMAL;DOMAIN:{str(rule.get('domain') or 'OTHER')}]"
                adjusted, consumed = backend.conditions.apply_condition_penalties(draft, tag)
                pending["mode"] = "DISADVANTAGE" if "MODE:DISADVANTAGE" in adjusted else "NORMAL"
                draft.pending_roll = pending
                backend.conditions._queue_pending_uses(draft, consumed)
                pending = draft.pending_roll
            values, natural = _dice(backend, pending.get("mode"))
            total = natural + (int(stats.get(rule["ability"], 10)) - 10) // 2
            dice.append({"kind": "d20", "values": values, "selected": natural})
            events.append({"type": "RollResolved", "actor_id": action.actor_id, "ability": rule["ability"], "dc": rule["dc"], "total": total, "success": total >= rule["dc"]})
            modifier = (int(stats.get(rule["ability"], 10)) - 10) // 2
            text += f"🎲 {rule['ability']}: {natural} {modifier:+d} = {total} против {rule['dc']} — {'успех' if total >= rule['dc'] else 'провал'}.\n"
            notices = _roll_commit(backend, draft, pending, action.actor_id)
            if notices:
                text += "\n".join(notices) + "\n"
        branch = rules.select_branch(rule, total)
        text += rule[branch]["text"]
        events += rules.apply_effects(draft, action.actor_id, rule[branch].get("effects", []), source_id=action.operation_id, backend=backend)
        if rule.get("once"):
            draft.local_object_uses[f"{object_id}:{rule_id}"] = action.operation_id
        events.append({"type": "ObjectInteractionResolved", "actor_id": action.actor_id, "object_id": object_id, "rule_id": rule_id, "branch": branch})
    else:
        # This is deliberate in-world waiting, never a scheduler timeout/absence.
        authored = getattr(draft, "local_wait_rule", None)
        if isinstance(authored, dict) and authored:
            rule = rules.validate_rule(authored)
            if rule["uncertain"]:
                raise LocalActionError("Ожидание не должно создавать случайный бросок.")
            rules.validate_targets(draft, action.actor_id, rule)
            events += rules.apply_effects(draft, action.actor_id, rule["success"].get("effects", []), source_id=action.operation_id, backend=backend)
            text = rule["success"]["text"]
        else:
            text = "⏳ Ты осознанно ждёшь; новых последствий для ожидания не задано."
        events.append({"type": "VoluntaryWait", "actor_id": action.actor_id})
    return text, prompt, events, dice


def execute_action(session, action: Action, *, backend: EngineBackend | None = None, ai_mode="balanced") -> ActionResult:
    """Validate -> isolated reduction -> one in-memory commit; caller persists."""
    backend = backend or EngineBackend()
    row = ensure(session)
    cached = row["operation_results"].get(action.operation_id)
    if cached:
        if cached.get("fingerprint") != _fingerprint(action):
            raise LocalActionError("Операция уже использована другим действием.")
        if action.campaign_id != str(getattr(session, "campaign_id", "") or "") or action.actor_id not in lifecycle.active_actor_ids(session):
            raise LocalActionError("Результат этой операции недоступен.")
        result = ActionResult(**copy.deepcopy(cached["result"]))
        result.replayed = True
        result.status = "replayed"
        return result
    validate_action(session, action)
    draft, names = _clone(session)
    prestate = _snapshot(draft, names)
    try:
        text, prompt, events, dice = _resolve(draft, action, backend)
        for sequence, event in enumerate(events):
            event["event_id"] = f"{action.operation_id}:{sequence}"
            event.setdefault("operation_id", action.operation_id)
        lifecycle.tick_event(draft, "action", action.operation_id, actor_id=action.actor_id)
        window = lifecycle.ensure(draft)["window"]
        resolved = window.setdefault("resolved_actors", [])
        if action.actor_id not in resolved:
            resolved.append(action.actor_id)
        # A confirmed local move replaces this actor's still unprocessed prose
        # declaration; the narrator must not execute it a second time.
        if isinstance(getattr(draft, "pending_actions", None), dict):
            draft.pending_actions.pop(str(action.actor_id), None)
        expected = [uid for uid in window["actors"] if uid in lifecycle.active_actor_ids(draft)]
        window_closed = set(expected).issubset(resolved)
        if window_closed:
            lifecycle.close_window(draft, turn_id=action.turn_id)
            lifecycle.tick_event(draft, "window", action.turn_id)
        else:
            # One addressed roll is done, but other declarations in a group
            # retain the same logical window and become actionable again.
            lifecycle.ensure(draft)["window"]["phase"] = "input"
        round_complete = lifecycle.note_actor_resolved(draft, action.actor_id)
        if round_complete:
            lifecycle.tick_event(draft, "round", lifecycle.ensure(draft)["round_id"])
        draft.state = "RESOLVING" if window_closed else "WAITING_ACTION"
        draft.pending_roll = None
        meaningful = any(event.get("meaningful") for event in events)
        needs_narrator = bool(prompt) or meaningful or round_complete
        # economy postpones decorative summaries, never unknown consequences or
        # critical NPC/world content. Known mechanics always deliver immediately.
        if ai_mode == "economy" and not prompt and not meaningful and not round_complete:
            needs_narrator = False
        next_actor = next_actor_id(draft, action.actor_id) if window_closed else next((uid for uid in expected if uid not in resolved), None)
        result = ActionResult(action.operation_id, text, events, dice, prompt, needs_narrator, round_complete, window_closed, next_actor)
        draft_row = ensure(draft)
        record = {"operation_id": action.operation_id, "campaign_id": action.campaign_id, "turn_id": action.turn_id,
                  "kind": action.kind, "public_summary": text,
                  "actor_id": action.actor_id, "action": dataclasses.asdict(action), "pre_state": prestate,
                  "result": dataclasses.asdict(result), "created_at": time.time()}
        draft_row["action_records"] = (draft_row["action_records"] + [record])[-RECORD_LIMIT:]
        draft_row["operation_order"] = (draft_row["operation_order"] + [action.operation_id])[-RECORD_LIMIT:]
        draft_row["operation_results"][action.operation_id] = {"fingerprint": _fingerprint(action), "result": dataclasses.asdict(result)}
        draft_row["operation_results"] = {key: draft_row["operation_results"][key] for key in draft_row["operation_order"]}
        setattr(draft, STATE_FIELD, draft_row)
        draft.action_records = draft_row["action_records"]
        for name in set(vars(draft)) - set(vars(session)):
            value = getattr(draft, name)
            try:
                json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            names.add(name)
        # Check serializability before swapping any authoritative field.
        committed = {name: copy.deepcopy(getattr(draft, name)) for name in names}
        json.dumps(committed, ensure_ascii=False)
    except LocalActionError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise LocalActionError(str(exc)) from exc
    for name, value in committed.items():
        setattr(session, name, value)
    return result


def next_actor_id(session, after_actor_id=None) -> int | None:
    actors = lifecycle.active_actor_ids(session)
    if not actors:
        return None
    if after_actor_id in actors:
        return actors[(actors.index(after_actor_id) + 1) % len(actors)]
    return actors[0]


def choose_enemy_action(session, enemy_id: str) -> dict | None:
    """Only explicitly opted-in simple NPCs; absence never triggers an attack."""
    enemy = (getattr(session, "enemy_combatants", {}) or {}).get(str(enemy_id))
    if not isinstance(enemy, dict) or enemy.get("status") == "dead" or int(enemy.get("hp", 0)) <= 0:
        return None
    strategy = (getattr(session, "local_enemy_rules", {}) or {}).get(str(enemy_id))
    if not isinstance(strategy, dict) or strategy.get("policy") != "simple":
        return None
    if strategy.get("retreated"):
        return None
    window = lifecycle.current_window(session)
    if window and window.get("status") == "open":
        return None  # party reaction/declaration must resolve first
    intent = getattr(session, "enemy_intent", None)
    if isinstance(intent, dict) and not intent.get("due"):
        return None
    actors = lifecycle.active_actor_ids(session)
    if not actors:
        return None
    hp_fraction = int(enemy["hp"]) / max(1, int(enemy.get("max_hp", enemy["hp"])))
    if hp_fraction <= float(strategy.get("retreat_below", 0.2)):
        return {"kind": "retreat", "enemy_id": str(enemy_id)}
    allowed = strategy.get("reachable_actor_ids")
    targets = [uid for uid in actors if allowed is None or uid in allowed]
    if not targets:
        return {"kind": "seek_cover", "enemy_id": str(enemy_id)}
    # Deterministic target selection: most vulnerable reachable present hero.
    target = min(targets, key=lambda uid: (int(((getattr(session, "character_sheets", {}) or {}).get(str(uid)) or {}).get("hp", 1)), uid))
    return {"kind": "attack", "enemy_id": str(enemy_id), "target_user_id": target,
            "power": str(enemy.get("power") or "MEDIUM"), "reason": str(strategy.get("attack_text") or f"{enemy.get('name', enemy_id)} атакует")}


def execute_enemy_action(session, enemy_id: str, *, operation_id: str, backend: EngineBackend) -> ActionResult:
    """One opted-in NPC move after a completed explicit round; persist before send.

    The caller must never call this from a timeout/skip handler. A completed
    round of genuinely resolved player actions is required here as well.
    """
    row = ensure(session)
    cached = row["operation_results"].get(str(operation_id))
    if cached:
        if cached.get("enemy_id") != str(enemy_id) or cached.get("campaign_id") != str(getattr(session, "campaign_id", "")):
            raise LocalActionError("Операция противника уже использована.")
        result = ActionResult(**copy.deepcopy(cached["result"]))
        result.replayed, result.status = True, "replayed"
        return result
    if getattr(session, "paused", False) or getattr(session, "pending_generation_request", None) or getattr(session, "pending_generated_result", None):
        raise LocalActionError("Ход противника заблокирован паузой или восстановлением.")
    life = lifecycle.ensure(session)
    actors = lifecycle.active_actor_ids(session)
    round_id = life.get("round_id")
    if not actors or not round_id or not set(actors).issubset(life.get("round_acted", [])):
        raise LocalActionError("Сначала должны завершиться реальные действия раунда.")
    if row["scheduler"].get("enemy_acted_round", {}).get(str(enemy_id)) == round_id:
        raise LocalActionError("Этот противник уже действовал в раунде.")
    decision = choose_enemy_action(session, str(enemy_id))
    if decision is None:
        raise LocalActionError("Известный ход противника сейчас недоступен.")
    draft, names = _clone(session)
    prestate = _snapshot(draft, names)
    before = copy.deepcopy(getattr(draft, "character_sheets", {}) or {})
    enemy = draft.enemy_combatants[str(enemy_id)]
    prompt = ""
    if decision["kind"] == "attack":
        attack = dict(decision, enemy_name=enemy["name"], enemy_hp=enemy["hp"], enemy_ac=enemy["ac"])
        text, prompt, all_dead = backend.combat._resolve_enemy_attack(draft, attack)
        after = copy.deepcopy(draft.character_sheets)
        changed = [uid for uid in before if before[uid].get("hp") != (after.get(uid) or {}).get("hp") or before[uid].get("status") != (after.get(uid) or {}).get("status")]
        meaningful = bool(all_dead) or any(before[uid].get("status") != after[uid].get("status") for uid in changed)
        event = {"type": "EnemyActionResolved", "enemy_id": str(enemy_id), "kind": "attack", "target_id": decision["target_user_id"], "before": before, "after": after, "meaningful": meaningful, "result_text": text}
        if not meaningful:
            prompt = ""
    elif decision["kind"] == "retreat":
        draft.local_enemy_rules[str(enemy_id)]["retreated"] = True
        text = f"👹 {enemy['name']} отступает по известному правилу поведения."
        event = {"type": "EnemyActionResolved", "enemy_id": str(enemy_id), "kind": "retreat", "meaningful": True, "text": text}
    else:
        text = f"👹 {enemy['name']} ищет укрытие: доступной цели для атаки нет."
        event = {"type": "EnemyActionResolved", "enemy_id": str(enemy_id), "kind": "seek_cover", "meaningful": False, "text": text}
    event.update(event_id=f"{operation_id}:0", operation_id=str(operation_id))
    result = ActionResult(str(operation_id), text, [event], [{"kind": "enemy_resolution", "detail": text}], prompt, bool(event.get("meaningful")))
    draft_row = ensure(draft)
    draft_row["scheduler"].setdefault("enemy_acted_round", {})[str(enemy_id)] = round_id
    draft_row["action_records"] = (draft_row["action_records"] + [{"operation_id": str(operation_id), "campaign_id": str(getattr(session, "campaign_id", "")), "turn_id": (lifecycle.current_window(session) or {}).get("turn_id"), "kind": "ENEMY_ACTION", "public_summary": text, "action": decision, "pre_state": prestate, "result": dataclasses.asdict(result), "created_at": time.time()}])[-RECORD_LIMIT:]
    draft_row["operation_order"] = (draft_row["operation_order"] + [str(operation_id)])[-RECORD_LIMIT:]
    draft_row["operation_results"][str(operation_id)] = {"enemy_id": str(enemy_id), "campaign_id": str(getattr(session, "campaign_id", "")), "result": dataclasses.asdict(result)}
    draft_row["operation_results"] = {key: draft_row["operation_results"][key] for key in draft_row["operation_order"]}
    setattr(draft, STATE_FIELD, draft_row)
    draft.action_records = draft_row["action_records"]
    committed = {name: copy.deepcopy(getattr(draft, name)) for name in names}
    json.dumps(committed, ensure_ascii=False)
    for name, value in committed.items():
        setattr(session, name, value)
    return result


def gameplay_journal(session, *, limit=12) -> list[str]:
    """Public outcomes, including misses that create no canonical state delta."""
    records = ensure(session)["action_records"][-max(0, min(100, int(limit))):]
    return [str((record.get("result") or {}).get("text") or "") for record in records if (record.get("result") or {}).get("text")]
