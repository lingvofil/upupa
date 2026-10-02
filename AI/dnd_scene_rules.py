"""Small declarative contracts for known object interactions.

A scene object can contain ``interactions`` keyed by stable rule ID. Each rule
has label, uncertain, optional ability/DC, success/failure branches containing
text and typed effects. Only an uncertain rule with an explicit failure price
may ask for a roll. There is no natural-language action inference or eval.
"""
from __future__ import annotations

import copy
import json
import logging
import re


ABILITIES = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
EFFECT_KINDS = {"clock", "object", "item", "fact", "position"}
RULES_PROTOCOL = """Для заметных взаимодействий сцены задавай локальные правила для существующего SCENE объекта
или объекта, объявленного [SCENE:UPSERT] выше в ЭТОМ ЖЕ ответе:
<DND_RULES>{"cart":{"push":{"label":"Толкнуть телегу","uncertain":true,"ability":"STR","dc":12,"failure_price":true,"once":true,"success":{"text":"Телега перекрыла проход.","effects":[{"kind":"object","object_id":"cart","state":"перекрывает проход"}]},"failure":{"text":"Телега загрохотала.","effects":[{"kind":"clock","clock_id":"alarm","delta":1}]}}}}</DND_RULES>
Когда содержательная сцена заканчивается ACTION:INPUT, постарайся дать 1–3 публичных быстрых действия, если в сцене есть конкретные объекты/возможности.
Только существующие или объявленные в том же ответе object_id/clock_id. Для очевидного действия uncertain:false и success, без DC. Своя идея всегда разрешена отдельно.
Не задавай бросок без сохранённой цены провала. Не назначай цены за отсутствие игрока или время ответа. Скрытые правила помечай public:false.
Это служебный JSON; художественный текст и финальный ACTION остаются отдельно.""".strip()
_RULES_RE = re.compile(r"<DND_RULES>(.*?)</DND_RULES>", re.S | re.I)
ENEMY_RULES_PROTOCOL = """Для УЖЕ зарегистрированного простого врага можно задать известное поведение:
<DND_ENEMIES>{"огр":{"policy":"simple","retreat_below":0.2,"attack_text":"Огр бьёт ближайшего доступного героя"}}</DND_ENEMIES>
Ключ — точный сохранённый ID врага. reachable_actor_ids необязателен: массив ID действительно доступных героев.
Не включай simple для NPC со сложной мотивацией или неизвестными сюжетными последствиями. Пропуск отсутствующего героя не запускает атаку.
""".strip()
_ENEMIES_RE = re.compile(r"<DND_ENEMIES>(.*?)</DND_ENEMIES>", re.S | re.I)
WAIT_RULES_PROTOCOL = """Для осознанного ожидания с известным исходом в текущей сцене задай <DND_WAIT>JSON</DND_WAIT>:
JSON — правило с label, uncertain:false, public:true, success:{text,effects}; effects как DND_RULES.
Таймер, пауза, отсутствие игрока и просмотр меню никогда не исполняют DND_WAIT.""".strip()
_WAIT_RE = re.compile(r"<DND_WAIT>(.*?)</DND_WAIT>", re.S | re.I)


class SceneRuleError(ValueError):
    pass


def apply_wait_rule_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    matches = list(_WAIT_RE.finditer(str(original_text or "")))
    if not matches:
        return cleaned, notices
    try:
        if len(matches) != 1 or len(matches[0].group(1)) > 8_000:
            raise SceneRuleError("One bounded wait contract expected")
        rule = validate_rule(json.loads(matches[0].group(1)))
        if rule["uncertain"]:
            raise SceneRuleError("Wait cannot create an unannounced random check")
        for effect in rule["success"].get("effects", []):
            if effect["kind"] == "clock" and str(effect["clock_id"]) not in (getattr(session, "scene_clocks", {}) or {}):
                raise SceneRuleError("Unknown wait clock")
            if effect["kind"] == "object" and str(effect["object_id"]) not in (getattr(session, "scene_objects", {}) or {}):
                raise SceneRuleError("Unknown wait object")
        session.local_wait_rule = rule
    except (SceneRuleError, TypeError, ValueError):
        logging.warning("DnD invalid wait contract ignored chat_id=%s", getattr(session, "chat_id", None))
    return _WAIT_RE.sub("", str(cleaned or "")).strip(), notices


def _text(value, limit=500):
    return str(value or "").strip()[:limit]


def _int(value, low, high):
    if type(value) is not int:
        raise SceneRuleError("Expected a bounded integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise SceneRuleError("Expected a bounded integer") from None
    if result < low or result > high:
        raise SceneRuleError("Integer outside rule bounds")
    return result


def validate_rule(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise SceneRuleError("Unknown interaction")
    rule = copy.deepcopy(raw)
    if set(rule) - {"label", "uncertain", "ability", "dc", "failure_price", "success", "failure", "success_with_cost", "clean_success_margin", "once", "locations", "required_item", "domain", "public"}:
        raise SceneRuleError("Unknown interaction fields")
    rule["label"] = _text(rule.get("label"), 100)
    if not rule["label"]:
        raise SceneRuleError("Interaction needs a visible label")
    for key in ("uncertain", "failure_price", "once", "public"):
        if key in rule and type(rule[key]) is not bool:
            raise SceneRuleError(f"{key} must be a JSON boolean")
    rule["uncertain"] = rule.get("uncertain", False)
    locations = rule.get("locations")
    if locations is not None and (not isinstance(locations, list) or len(locations) > 12 or any(not isinstance(v, str) for v in locations)):
        raise SceneRuleError("locations must be a short list of saved location names")
    if rule.get("domain", "OTHER") not in {"MOVE", "SOCIAL", "PERCEPTION", "COMBAT", "OTHER"}:
        raise SceneRuleError("Unknown check domain")
    if rule["uncertain"]:
        rule["ability"] = str(rule.get("ability") or "").upper()
        if rule["ability"] not in ABILITIES:
            raise SceneRuleError("Known check needs a valid ability")
        rule["dc"] = _int(rule.get("dc"), 5, 20)
        if not rule.get("failure_price"):
            raise SceneRuleError("A repeatable check without a price is not a roll")
    elif rule.get("dc") is not None:
        raise SceneRuleError("Certain interaction must not have DC")
    for name in ("success", "failure", "success_with_cost"):
        branch = rule.get(name)
        if branch is None and (name == "success_with_cost" or (name == "failure" and not rule["uncertain"])):
            continue
        if not isinstance(branch, dict) or not _text(branch.get("text")):
            raise SceneRuleError("Known outcome needs authored text")
        branch["text"] = _text(branch["text"], 900)
        effects = branch.get("effects", [])
        if not isinstance(effects, list) or len(effects) > 12:
            raise SceneRuleError("Invalid consequence list")
        for effect in effects:
            if not isinstance(effect, dict) or effect.get("kind") not in EFFECT_KINDS:
                raise SceneRuleError("Unknown consequence kind")
            kind = effect["kind"]
            if kind == "clock":
                if not effect.get("clock_id"):
                    raise SceneRuleError("Clock consequence needs existing clock ID")
                effect["delta"] = _int(effect.get("delta"), -4, 4)
            elif kind == "object":
                if not effect.get("object_id") or ("state" not in effect and "available" not in effect):
                    raise SceneRuleError("Object consequence needs target and change")
                if "available" in effect and type(effect["available"]) is not bool:
                    raise SceneRuleError("available must be a JSON boolean")
            elif kind == "item":
                if effect.get("operation") not in {"add", "remove"} or not effect.get("name"):
                    raise SceneRuleError("Item consequence needs explicit operation/name")
                effect["quantity"] = _int(effect.get("quantity", 1), 1, 99)
            elif kind == "position" and not effect.get("location"):
                raise SceneRuleError("Position consequence needs a location")
            elif kind == "fact" and not _text(effect.get("text")):
                raise SceneRuleError("Fact consequence needs authored text")
        if name == "failure" and rule["uncertain"] and not effects:
            raise SceneRuleError("Failure price must change saved state")
    if rule.get("success_with_cost") is not None:
        rule["clean_success_margin"] = _int(rule.get("clean_success_margin", 5), 1, 10)
    return rule


def apply_scene_rule_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    """Validate an entire JSON update before changing any object's rules."""
    source = str(original_text or "")
    matches = list(_RULES_RE.finditer(source))
    output = _RULES_RE.sub("", str(cleaned or "")).strip()
    if not matches:
        return output, list(notices or [])
    proposed = {}
    objects = getattr(session, "scene_objects", {}) or {}
    clocks = getattr(session, "scene_clocks", {}) or {}
    try:
        if len(matches) != 1 or len(matches[0].group(1)) > 12000:
            raise SceneRuleError("One bounded DND_RULES block expected")
        payload = json.loads(matches[0].group(1))
        if not isinstance(payload, dict) or not payload or len(payload) > 8:
            raise SceneRuleError("Invalid object rule map")
        for object_id, raw_rules in payload.items():
            if object_id not in objects or not isinstance(raw_rules, dict) or not raw_rules or len(raw_rules) > 8:
                raise SceneRuleError("Rules must refer to existing scene objects")
            prepared = {}
            for rule_id, raw_rule in raw_rules.items():
                if not isinstance(rule_id, str) or not rule_id or len(rule_id) > 64:
                    raise SceneRuleError("Invalid stable rule ID")
                rule = validate_rule(raw_rule)
                for branch in (rule.get("success"), rule.get("failure"), rule.get("success_with_cost")):
                    for effect in (branch or {}).get("effects", []):
                        if effect.get("actor_id") is not None:
                            raise SceneRuleError("Metadata interactions apply to their invoking actor")
                        if effect["kind"] == "object" and str(effect["object_id"]) not in objects:
                            raise SceneRuleError("Unknown object consequence")
                        if effect["kind"] == "clock" and str(effect["clock_id"]) not in clocks:
                            raise SceneRuleError("Unknown clock consequence")
                prepared[rule_id] = rule
            proposed[object_id] = prepared
    except (ValueError, TypeError, KeyError) as exc:
        logging.warning("DnD local scene rules rejected: %s", exc)
        return output, list(notices or [])
    for object_id, prepared in proposed.items():
        objects[object_id].setdefault("interactions", {}).update(prepared)
    return output, list(notices or [])


def apply_enemy_rule_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    """Atomic opt-in for registered NPC policies, never an inferred behaviour."""
    matches = list(_ENEMIES_RE.finditer(str(original_text or "")))
    output = _ENEMIES_RE.sub("", str(cleaned or "")).strip()
    if not matches:
        return output, list(notices or [])
    prepared = {}
    try:
        if len(matches) != 1 or len(matches[0].group(1)) > 4000:
            raise SceneRuleError("One bounded DND_ENEMIES block expected")
        payload = json.loads(matches[0].group(1))
        if not isinstance(payload, dict) or not payload or len(payload) > 8:
            raise SceneRuleError("Invalid enemy policy map")
        for enemy_id, raw in payload.items():
            if enemy_id not in (getattr(session, "enemy_combatants", {}) or {}):
                raise SceneRuleError("Enemy policy needs a registered enemy ID")
            if not isinstance(raw, dict) or set(raw) - {"policy", "retreat_below", "attack_text", "reachable_actor_ids"} or raw.get("policy") != "simple":
                raise SceneRuleError("Unknown enemy policy")
            retreat = raw.get("retreat_below", 0.2)
            if isinstance(retreat, bool) or not isinstance(retreat, (int, float)) or not 0 <= retreat <= 0.8:
                raise SceneRuleError("Invalid retreat threshold")
            policy = {"policy": "simple", "retreat_below": float(retreat), "attack_text": _text(raw.get("attack_text"), 240)}
            reachable = raw.get("reachable_actor_ids")
            if reachable is not None:
                if not isinstance(reachable, list) or len(reachable) > 24 or any(isinstance(uid, bool) or not isinstance(uid, int) or str(uid) not in (getattr(session, "participants", {}) or {}) for uid in reachable):
                    raise SceneRuleError("Unknown reachable participant")
                policy["reachable_actor_ids"] = list(dict.fromkeys(reachable))
            prepared[enemy_id] = policy
    except (ValueError, TypeError, KeyError) as exc:
        logging.warning("DnD simple enemy policies rejected: %s", exc)
        return output, list(notices or [])
    existing = copy.deepcopy(getattr(session, "local_enemy_rules", {}) or {})
    existing.update(prepared)
    session.local_enemy_rules = existing
    return output, list(notices or [])


def get_interaction(session, object_id: str, rule_id: str) -> dict:
    obj = (getattr(session, "scene_objects", {}) or {}).get(str(object_id))
    if not isinstance(obj, dict) or not obj.get("available", True):
        raise SceneRuleError("Object unavailable")
    rules = obj.get("interactions")
    if not isinstance(rules, dict) or str(rule_id) not in rules:
        raise SceneRuleError("This interaction has no known local rules")
    rule = validate_rule(rules[str(rule_id)])
    if rule.get("once") and f"{object_id}:{rule_id}" in (getattr(session, "local_object_uses", {}) or {}):
        raise SceneRuleError("This interaction has already been resolved")
    return rule


def validate_targets(session, actor_id: int, rule: dict) -> None:
    for branch in (rule.get("success"), rule.get("failure"), rule.get("success_with_cost")):
        if not isinstance(branch, dict):
            continue
        for effect in branch.get("effects", []):
            kind = effect["kind"]
            if kind == "clock" and str(effect["clock_id"]) not in (getattr(session, "scene_clocks", {}) or {}):
                raise SceneRuleError("Unknown consequence clock")
            if kind == "object" and str(effect["object_id"]) not in (getattr(session, "scene_objects", {}) or {}):
                raise SceneRuleError("Unknown consequence object")
            if effect.get("actor_id") is not None and str(effect["actor_id"]) != str(actor_id):
                raise SceneRuleError("Known interaction cannot impersonate another actor")
    locations = rule.get("locations")
    if locations:
        location = ((getattr(session, "player_positions", {}) or {}).get(str(actor_id)) or {}).get("location")
        if location not in locations:
            raise SceneRuleError("Object cannot be reached from the saved position")
    required = rule.get("required_item")
    if required and not any((item.get("name") if isinstance(item, dict) else str(item)) == required for item in (getattr(session, "inventories", {}) or {}).get(str(actor_id), [])):
        raise SceneRuleError("Required item is not owned")


def select_branch(rule: dict, total: int | None) -> str:
    if not rule["uncertain"]:
        return "success"
    if total is None:
        raise SceneRuleError("Uncertain interaction needs a saved roll")
    if total < rule["dc"]:
        return "failure"
    if rule.get("success_with_cost") and total < rule["dc"] + rule["clean_success_margin"]:
        return "success_with_cost"
    return "success"


def apply_effects(session, actor_id: int, effects: list[dict], *, source_id: str, backend=None) -> list[dict]:
    """Apply authored consequences on the executor's isolated draft only."""
    events = []
    for index, effect in enumerate(effects):
        kind = effect["kind"]
        event = {"event_id": f"{source_id}:{index}", "type": "KnownConsequence", "kind": kind, "actor_id": actor_id}
        if kind == "clock":
            key = str(effect["clock_id"])
            row = session.scene_clocks[key]
            before = int(row.get("value", 0))
            maximum = int(row.get("max", 6))
            if not row.get("full"):
                row["value"] = max(0, min(maximum, before + effect["delta"]))
                actual = row["value"] - before
                if actual:
                    cause = _text(effect.get("cause") or "известное последствие действия", 220)
                    row["history"] = (list(row.get("history") or []) + [{"delta": actual, "cause": cause, "operation_id": source_id}])[-12:]
                    if row["value"] >= maximum:
                        row.update(full=True, full_cause=cause)
            event.update(type="ClockAdvanced", clock_id=key, before=before, after=int(row.get("value", before)), full=bool(row.get("full")))
            if not before >= maximum and event["full"]:
                event["meaningful"] = True
                event["world_fact"] = str(row.get("when_full") or "")
        elif kind == "object":
            key = str(effect["object_id"])
            row = session.scene_objects[key]
            before = copy.deepcopy(row)
            if "state" in effect:
                row["state"] = _text(effect["state"], 180)
            if "available" in effect:
                row["available"] = bool(effect["available"])
            event.update(type="ObjectChanged", object_id=key, before=before, after=copy.deepcopy(row))
        elif kind == "item":
            inventory = session.inventories.setdefault(str(actor_id), [])
            name = str(effect["name"])
            found = next((item for item in inventory if isinstance(item, dict) and item.get("name") == name), None)
            quantity = effect["quantity"]
            before = int((found or {}).get("quantity", 1)) if found else 0
            if effect["operation"] == "add":
                if found:
                    found["quantity"] = before + quantity
                else:
                    inventory.append({"name": name, "kind": "item", "quantity": quantity})
                after = before + quantity
            else:
                if found is None or before < quantity:
                    raise SceneRuleError("Consequence would remove an unowned item")
                after = before - quantity
                if after:
                    found["quantity"] = after
                else:
                    inventory.remove(found)
            event.update(type="ItemChanged", item_name=name, before=before, after=after)
        elif kind == "position":
            positions = getattr(session, "player_positions", None)
            if not isinstance(positions, dict):
                positions = session.player_positions = {}
            before = copy.deepcopy(positions.get(str(actor_id)))
            positions[str(actor_id)] = {"location": _text(effect["location"], 120), "detail": _text(effect.get("detail"), 180)}
            event.update(type="PositionChanged", before=before, after=copy.deepcopy(positions[str(actor_id)]))
        else:
            facts = getattr(session, "local_world_facts", [])
            fact = {"id": event["event_id"], "text": _text(effect["text"]), "actor_id": actor_id}
            session.local_world_facts = (list(facts) + [fact])[-100:]
            event.update(type="WorldFactEstablished", text=fact["text"], meaningful=bool(effect.get("meaningful", False)))
        events.append(event)
    return events
