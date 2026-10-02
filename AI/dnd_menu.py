"""Snapshot-only Telegram navigation and guarded, actor-owned action prompts.

The menu never generates text or applies game mechanics. Its execution hook
receives a typed request after the same identity and ownership checks for every
callback. UI bookkeeping is deliberately separate from canonical game state.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import secrets
import time

from aiogram import BaseMiddleware, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_PREFIX = "dnd:m:"
MENU_COMMANDS = {"днд меню", "упупа днд меню"}
_FIELDS = (
    "campaign_id", "turn_id", "state_revision", "state", "mode", "scene_count",
    "mission_goal", "selected_plot", "scene_log", "participants", "character_profiles",
    "character_sheets", "inventories", "reputations", "player_positions", "scene_objects",
    "enemy_combatants", "enemy_intent", "scene_clocks", "threat", "pending_actions",
    "pending_roll", "pending_poll", "action_target_user_ids", "action_prompt_message_id", "action_deadline",
    "action_records", "event_journal", "paused", "dnd_paused", "local_engine_state",
    "local_wait_rule",
)
_PAGES = {"overview", "hero", "items", "scene", "enemies", "party", "journal", "manage"}
_PROFILE_LABELS = {"style": "Образ", "strength": "Сильная сторона", "weakness": "Слабость", "special": "Особый приём"}
_ABILITY_LABELS = {"STR": "Сила", "DEX": "Ловкость", "CON": "Телосложение", "INT": "Интеллект", "WIS": "Мудрость", "CHA": "Харизма"}
_STATES = {"LOBBY": "Собираем участников", "WAITING_MODE": "Выбираем режим", "WAITING_PLOT": "Выбираем сюжет",
           "WAITING_ACTION": "Ждём действий", "WAITING_ROLL": "Ждём бросок", "WAITING_POLL": "Голосование",
           "WAITING_HEAL": "Решение о лечении", "RESOLVING": "Разрешаем последствия"}
_ITEM_MECHANICS = {"ADVANTAGE_MOVE", "ADVANTAGE_SOCIAL", "ADVANTAGE_PERCEPTION", "ADVANTAGE_COMBAT",
                   "CLEAR_CONDITION", "CREATE_EXIT", "CLOCK_PUSH", "CLOCK_COMPLETE", "CLEAR_ACCUSATION"}


def snapshot_session(session) -> dict:
    """Copy public-state sources without calling live ensure/repair helpers."""
    return {key: copy.deepcopy(getattr(session, key, None)) for key in _FIELDS}


def _text(value, limit=400) -> str:
    value = re.sub(r"\[(?:ACTION|ITEM|NPC|POSITION|SCENE|INTENT|CLOCK|THREAT|META):[^\]]*\]", "", str(value or ""), flags=re.I)
    return " ".join(value.split())[:limit]


def _public(row) -> bool:
    return not isinstance(row, dict) or not (
        row.get("hidden") or row.get("secret") or row.get("is_hidden") or row.get("public") is False
        or str(row.get("visibility", "public")).casefold() in {"private", "secret", "hidden", "dm"}
    )


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _name(snapshot, user_id) -> str:
    return _text(_mapping(_mapping(snapshot.get("participants")).get(str(user_id))).get("name") or "Игрок", 70)


def _visible_items(snapshot, user_id):
    rows = _mapping(snapshot.get("inventories")).get(str(user_id)) or []
    return [(index, row) for index, row in enumerate(rows) if _public(row) and _item_name(row)]


def _item_name(item):
    return _text(item.get("name") if isinstance(item, dict) else item, 100)


def _item_ref(index, item):
    digest = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"item_id": str(item.get("id") or f"legacy:{digest[:20]}") if isinstance(item, dict) else f"legacy:{digest[:20]}",
            "item_name": _item_name(item), "item_index": index, "item_fingerprint": digest}


def _clock_lines(snapshot):
    lines = []
    for row in _mapping(snapshot.get("scene_clocks")).values():
        if not isinstance(row, dict) or not _public(row):
            continue
        maximum = max(1, _int(row.get("max"), 4))
        value = min(maximum, max(0, _int(row.get("value"))))
        lines.append(f"{'⚠️' if row.get('kind') == 'DANGER' else '🎯'} {_text(row.get('name') or row.get('id'), 80)}: {value}/{maximum}")
    return lines[:2]


def _hp(snapshot, user_id):
    sheet = _mapping(_mapping(snapshot.get("character_sheets")).get(str(user_id)))
    return f"❤️ {sheet.get('hp', 0)}/{sheet.get('max_hp', 0)} HP" if sheet else ""


def _ability_modifier(value) -> int:
    return (_int(value, 10) - 10) // 2


def _quick_interactions(snapshot, limit=3):
    result = []
    current_scene = _int(snapshot.get("scene_count"))
    for object_id, row in _mapping(snapshot.get("scene_objects")).items():
        if not isinstance(row, dict) or not _public(row) or not row.get("available", True):
            continue
        updated_scene = row.get("updated_scene")
        if updated_scene is not None and _int(updated_scene, -1) != current_scene:
            continue
        for rule_id, raw in _mapping(row.get("interactions")).items():
            rule = _public_interaction(raw)
            if rule is None:
                continue
            result.append((str(object_id), str(rule_id), rule))
            if len(result) >= limit:
                return result
    return result


def _wait_rule(snapshot):
    """Offer waiting only when a public, deterministic consequence is saved."""
    from AI.dnd_scene_rules import SceneRuleError, validate_rule

    raw = snapshot.get("local_wait_rule")
    if not isinstance(raw, dict) or not _public(raw):
        return None
    try:
        rule = validate_rule(raw)
    except SceneRuleError:
        return None
    return rule if not rule["uncertain"] else None


def _public_interaction(raw):
    """Only validated public contracts can become local action buttons."""
    from AI.dnd_scene_rules import SceneRuleError, validate_rule

    if not isinstance(raw, dict) or not _public(raw):
        return None
    try:
        rule = validate_rule(raw)
    except SceneRuleError:
        return None
    for name in ("success", "failure", "success_with_cost"):
        branch = rule.get(name)
        if branch is not None and (not _public(branch) or any(not _public(effect) for effect in branch.get("effects", []))):
            return None
    return rule


def _outcome_preview(snapshot, branch):
    lines = [_text(branch["text"], 400)]
    effects = branch.get("effects", [])
    for effect in effects[:4]:
        kind = effect["kind"]
        if kind == "clock":
            clock = _mapping(_mapping(snapshot.get("scene_clocks")).get(str(effect["clock_id"])))
            if clock and _public(clock):
                lines.append(f"{_text(clock.get('name'), 80)} {effect['delta']:+d}")
        elif kind == "object":
            obj = _mapping(_mapping(snapshot.get("scene_objects")).get(str(effect["object_id"])))
            if obj and _public(obj):
                changes = []
                if "state" in effect:
                    changes.append(_text(effect["state"], 100))
                if "available" in effect:
                    changes.append("доступен" if effect["available"] else "недоступен")
                lines.append(_text(obj.get("name"), 80) + ": " + ", ".join(changes))
        elif kind == "item":
            lines.append(f"{'Получить' if effect['operation'] == 'add' else 'Потратить'}: {_text(effect['name'], 80)} ×{effect['quantity']}")
        elif kind == "position":
            lines.append("Позиция: " + _text(effect["location"], 100))
        elif kind == "fact":
            lines.append(_text(effect["text"], 100))
    if len(effects) > 4:
        lines.append(f"Дополнительных эффектов: {len(effects) - 4}")
    return " · ".join(lines)


def render_journal(snapshot) -> str:
    """Project known public events; never print prompts, arbitrary data or secrets."""
    lines = []
    for row in (snapshot.get("event_journal") or [])[-24:]:
        if not isinstance(row, dict) or not _public(row):
            continue
        data = _mapping(row.get("data"))
        if any(isinstance(value, dict) and not _public(value) for value in data.values()):
            continue
        kind = row.get("type")
        if kind == "THREAT_CHANGED":
            before, after = _mapping(data.get("before")), _mapping(data.get("after"))
            label = f"Угроза: {before.get('level', 0)} → {after.get('level', 0)}"
        elif kind == "SCENE_CLOCK_CHANGED":
            before, after = _mapping(data.get("before")), _mapping(data.get("after"))
            label = f"{_text(after.get('name') or data.get('clock_id'), 60)}: {before.get('value', 0)} → {after.get('value', 0)}"
        elif kind in {"PLAYER_HP_CHANGED", "CHARACTER_HP_CHANGED", "HP_CHANGED"}:
            label = f"{_name(snapshot, data.get('player_id'))}: здоровье {data.get('before')} → {data.get('after')}"
        elif kind in {"ITEM_ADDED", "ITEM_REMOVED", "ITEM_QUANTITY_CHANGED", "ITEM_TRANSFERRED", "INVENTORY_CHANGED"}:
            label = "Инвентарь изменился" + (f": {_text(data.get('item_name'), 70)}" if data.get("item_name") else "")
        elif kind == "PLAYER_POSITION_CHANGED":
            after = _mapping(data.get("after"))
            label = f"{_name(snapshot, data.get('player_id'))}: {_text(after.get('location'), 80)}"
        else:
            continue
        lines.append(label)
    for row in (snapshot.get("action_records") or [])[-12:]:
        if not isinstance(row, dict) or not _public(row):
            continue
        result = _mapping(row.get("result"))
        if not _public(result):
            continue
        # Only designated public summaries belong in the player-facing journal.
        summary = row.get("public_summary") or result.get("public_summary")
        if summary:
            lines.append(_text(summary, 220))
        elif row.get("kind"):
            labels = {"ATTACK": "Атака", "ROLL": "Бросок", "USE_ITEM": "Предмет", "TRANSFER_ITEM": "Передача", "OBJECT": "Объект сцены"}
            label = labels.get(str(row.get("kind")).upper())
            if label:
                lines.append(f"{_name(snapshot, row.get('actor_id'))}: {label}")
    return "📖 Журнал событий\n\n" + ("\n".join("• " + line for line in lines[-16:]) or "Сохранённых публичных событий пока нет.")


def render_page(snapshot, page="overview", user_id=0) -> str:
    participants = _mapping(snapshot.get("participants"))
    if page == "hero":
        profile = _mapping(_mapping(snapshot.get("character_profiles")).get(str(user_id)))
        sheet = _mapping(_mapping(snapshot.get("character_sheets")).get(str(user_id)))
        stats = _mapping(sheet.get("stats"))
        lines = [f"👤 {_name(snapshot, user_id)}", _hp(snapshot, user_id)]
        if sheet.get("ac") is not None:
            lines.append(f"🛡 КБ {sheet['ac']}")
        if stats:
            lines.append("Характеристики:")
            for ability, label in _ABILITY_LABELS.items():
                if ability in stats:
                    score = _int(stats[ability], 10)
                    lines.append(f"• {label} ({ability}) {score} ({_ability_modifier(score):+d})")
        lines.extend(f"{label}: {_text(profile[key], 160)}" for key, label in _PROFILE_LABELS.items() if profile.get(key))
        position = _mapping(_mapping(snapshot.get("player_positions")).get(str(user_id)))
        if _public(position) and position.get("location"):
            lines.append("📍 " + _text(position.get("location"), 130))
        reputation = _mapping(snapshot.get("reputations")).get(str(user_id)) or []
        if reputation:
            lines.append("🏷 " + "; ".join(_text(value, 70) for value in reputation[-4:]))
        return "\n".join(line for line in lines if line) or "Герой ещё не создан."
    if page == "items":
        lines = [f"🎒 Вещи · {_name(snapshot, user_id)}"]
        for _, item in _visible_items(snapshot, user_id):
            row = _mapping(item)
            quantity = max(0, _int(row.get("quantity"), 1))
            lines.append(f"{'✨' if row.get('kind') == 'artifact' else '•'} {_item_name(item)} ×{quantity}")
            if row.get("description"):
                lines.append("  " + _text(row["description"], 180))
        return "\n".join(lines) if len(lines) > 1 else lines[0] + "\nПусто."
    if page == "enemies":
        lines = ["👹 Противники"]
        for row in _mapping(snapshot.get("enemy_combatants")).values():
            if isinstance(row, dict) and _public(row) and row.get("status") not in {"DEAD", "DEFEATED"}:
                lines.append(f"• {_text(row.get('name'), 90)} — {row.get('hp', 0)}/{row.get('max_hp', 0)} HP · КБ {row.get('ac', '?')}")
        return "\n".join(lines) if len(lines) > 1 else "👹 Сейчас нет известных активных противников."
    if page == "journal":
        return render_journal(snapshot)
    if page == "party":
        lines = ["👥 Партия"]
        for key, row in participants.items():
            if not isinstance(row, dict) or not _public(row):
                continue
            position = _mapping(_mapping(snapshot.get("player_positions")).get(str(key)))
            location = " · " + _text(position.get("location"), 70) if _public(position) and position.get("location") else ""
            lines.append(f"{'•' if row.get('active', True) else '○'} {_name(snapshot, key)} · {_hp(snapshot, key)}{location}")
        return "\n".join(lines)
    if page == "manage":
        return "⚙️ Управление партией\n\nНастройки: «днд настройки».\nПауза и восстановление продолжения доступны ведущему."
    scene = _text((snapshot.get("scene_log") or [""])[-1], 850)
    lines = [f"{'📍 Сцена' if page == 'scene' else '🎭 Партия'} {_int(snapshot.get('scene_count'))}"]
    if snapshot.get("mission_goal"):
        lines.append("🎯 " + _text(snapshot["mission_goal"], 240))
    lines.append(_STATES.get(snapshot.get("state"), "Игра идёт"))
    targets = snapshot.get("action_target_user_ids") or _mapping(snapshot.get("pending_roll")).get("target_user_ids") or []
    lines.append("Ход: " + (", ".join(_name(snapshot, value) for value in targets) if targets else "партии"))
    if snapshot.get("pending_actions"):
        active = sum(1 for row in participants.values() if isinstance(row, dict) and row.get("active", True))
        lines.append(f"Ответили: {len(snapshot['pending_actions'])}/{len(targets) or active}")
    deadline = snapshot.get("action_deadline")
    if snapshot.get("state") == "WAITING_ACTION" and deadline and len(targets) == 1:
        try:
            remaining = max(0, int(float(deadline) - time.time()))
        except (TypeError, ValueError):
            remaining = None
        if remaining is not None:
            minutes, seconds = divmod(remaining, 60)
            lines.append(f"⏱ Автопропуск через ~{minutes}:{seconds:02d}")
    if scene:
        lines.extend(["", scene])
    lines.extend(_clock_lines(snapshot))
    intent = _mapping(snapshot.get("enemy_intent"))
    if _public(intent) and intent.get("action"):
        lines.append("⚠️ " + _text(intent.get("enemy"), 70) + ": " + _text(intent["action"], 170))
    if page == "scene":
        for row in _mapping(snapshot.get("scene_objects")).values():
            if isinstance(row, dict) and _public(row):
                lines.append(f"• {_text(row.get('name') or row.get('id'), 80)}: {_text(row.get('state'), 130)}" + (" (использован)" if not row.get("available", True) else ""))
    return "\n".join(lines)


def _ensure_ui(session):
    if not isinstance(getattr(session, "menu_ui_state", None), dict):
        session.menu_ui_state = {"cards": {}, "tokens": {}, "seq": {}}
    for key in ("cards", "tokens", "seq"):
        if not isinstance(session.menu_ui_state.get(key), dict):
            session.menu_ui_state[key] = {}
    if not isinstance(getattr(session, "menu_info_message_ids", None), list):
        session.menu_info_message_ids = []
    if not isinstance(getattr(session, "menu_action_prompts", None), dict):
        session.menu_action_prompts = {}


class DndMenuService:
    def __init__(self, dnd, *, execute_action=None, identity_for=None):
        self.dnd = dnd
        self.execute_action = execute_action
        self.identity_for = identity_for
        self._locks = {}

    def _lock(self, chat_id, owner):
        return self._locks.setdefault((int(chat_id), int(owner)), asyncio.Lock())

    def identity(self, session):
        if self.identity_for:
            return dict(self.identity_for(session))
        engine = _mapping(getattr(session, "local_engine_state", None))
        turn = getattr(session, "turn_id", None) or engine.get("turn_id")
        if not turn:
            turn = f"{getattr(session, 'scene_count', 0)}:{getattr(session, 'action_prompt_message_id', 0)}:{getattr(session, 'state', '')}"
        return {"campaign_id": str(getattr(session, "campaign_id", "") or ""), "turn_id": str(turn),
                "expected_revision": _int(getattr(session, "state_revision", 0)), "phase": str(getattr(session, "state", ""))}

    def paused(self, session):
        return bool(getattr(session, "paused", False) or getattr(session, "dnd_paused", False)
                    or _mapping(getattr(session, "local_engine_state", None)).get("paused"))

    def _current(self, session, token):
        return all(token.get(key) == value for key, value in self.identity(session).items())

    def _window_current(self, session, prompt):
        identity = self.identity(session)
        return all(prompt.get(key) == identity.get(key) for key in ("campaign_id", "turn_id", "phase"))

    def _save(self):
        self.dnd.persist_dnd_sessions()

    def _token(self, session, owner, operation, *, card, seq, **payload):
        token = secrets.token_urlsafe(12)
        registry = session.menu_ui_state["tokens"]
        registry[token] = {**self.identity(session), "owner_id": int(owner), "operation": operation,
                           "card": card, "ui_seq": seq, "payload": payload, "created_at": time.time()}
        while len(registry) > 600:
            registry.pop(next(iter(registry)))
        return CALLBACK_PREFIX + token

    def _button(self, session, owner, text, operation, *, card, seq, **payload):
        return InlineKeyboardButton(text=text[:60], callback_data=self._token(session, owner, operation, card=card, seq=seq, **payload))

    def keyboard(self, session, owner, page, *, card, seq):
        snapshot = snapshot_session(session)
        button = lambda text, operation, **payload: self._button(session, owner, text, operation, card=card, seq=seq, **payload)
        rows = [[button("✍️ Свой ход", "compose")]] if snapshot.get("state") == "WAITING_ACTION" else []
        if owner and snapshot.get("state") == "WAITING_ROLL":
            rows.append([button("🎲 Бросить", "confirm", kind="ROLL")])
        if owner and page == "overview" and _wait_rule(snapshot):
            rows.append([button("⏳ Подождать", "confirm", kind="WAIT")])
        if owner and page == "hero" and snapshot.get("state") == "LOBBY" and str(owner) in _mapping(snapshot.get("participants")):
            from AI.dnd_character_templates import ARCHETYPES

            choices = list(ARCHETYPES)
            rows.extend([[button("🎯 " + choice.capitalize(), "confirm", kind="CHOOSE_ARCHETYPE", choice=choice)
                          for choice in choices[index:index + 2]] for index in range(0, len(choices), 2)])
        if page == "overview":
            for object_id, rule_id, rule in _quick_interactions(snapshot):
                rows.append([button("⚡ " + _text(rule.get("label") or rule_id, 48), "confirm",
                                    kind="OBJECT", object_id=object_id, inputs={"rule_id": rule_id})])
            for object_id, row in list(_mapping(snapshot.get("scene_objects")).items())[:3]:
                if isinstance(row, dict) and _public(row) and row.get("available", True):
                    rows.append([button("🔎 " + _text(row.get("name") or object_id, 42), "object", object_id=str(object_id))])
        if page == "scene":
            for object_id, row in list(_mapping(snapshot.get("scene_objects")).items())[:6]:
                if isinstance(row, dict) and _public(row) and row.get("available", True):
                    rows.append([button("🔎 " + _text(row.get("name") or object_id, 42), "object", object_id=str(object_id))])
        if owner and page == "items":
            for index, item in _visible_items(snapshot, owner)[:8]:
                ref = _item_ref(index, item)
                row = _mapping(item)
                operation = "confirm" if str(row.get("mechanic") or "").upper() in _ITEM_MECHANICS and str(row.get("requirement") or "NONE").upper() == "NONE" else "item_idea"
                rows.append([button("🧰 " + _item_name(item)[:32], operation, kind="USE_ITEM", **ref),
                             button("Передать", "recipients", **ref)])
        if owner and page == "enemies":
            for enemy_id, row in list(_mapping(snapshot.get("enemy_combatants")).items())[:6]:
                if isinstance(row, dict) and _public(row) and _int(row.get("hp"), 1) > 0 and row.get("status") not in {"DEAD", "DEFEATED"}:
                    rows.append([button("⚔️ " + _text(row.get("name"), 40), "confirm", kind="ATTACK", target_id=str(enemy_id))])
        labels = [("👤 Герой", "hero"), ("🎒 Вещи", "items"), ("📍 Сцена", "scene"), ("👹 Враги", "enemies"),
                  ("👥 Партия", "party"), ("📖 Журнал", "journal"), ("⚙️ Управление", "manage"), ("🔄 Обновить", page)]
        rows.extend([[button(text, "nav", page=target) for text, target in labels[index:index + 2]] for index in range(0, len(labels), 2)])
        if owner and page == "manage" and self.dnd._user_is_host(session, owner):
            rows.append([button("▶️ Продолжить" if self.paused(session) else "⏸ Пауза", "confirm", kind="RESUME" if self.paused(session) else "PAUSE")])
            rows.append([button("↻ Повторить продолжение", "confirm", kind="RETRY")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _deliver_card(self, bot, session, owner, text, markup):
        card = "party" if not owner else str(owner)
        old_id = session.menu_ui_state["cards"].get(card)
        if old_id:
            try:
                await bot.edit_message_text(text, chat_id=session.chat_id, message_id=int(old_id), reply_markup=markup, parse_mode=None)
                return int(old_id)
            except TelegramBadRequest as error:
                message = str(error).casefold()
                if "message is not modified" in message:
                    return int(old_id)
                if "message to edit not found" not in message:
                    raise
                # A definitive deleted-card response permits one replacement.
        sent = await bot.send_message(session.chat_id, text, reply_markup=markup, parse_mode=None)
        session.menu_ui_state["cards"][card] = int(sent.message_id)
        if int(sent.message_id) not in session.menu_info_message_ids:
            session.menu_info_message_ids.append(int(sent.message_id))
        return int(sent.message_id)

    async def show(self, bot, session, owner=0, page="overview", *, text=None, build_markup=None):
        """Caller holds the card lock; all navigation renders the newest snapshot."""
        _ensure_ui(session)
        card = "party" if not owner else str(owner)
        seq = _int(session.menu_ui_state["seq"].get(card)) + 1
        session.menu_ui_state["seq"][card] = seq
        markup = build_markup(card, seq) if build_markup else self.keyboard(session, owner, page, card=card, seq=seq)
        if text is None:
            text = render_page(snapshot_session(session), page, owner)
            if self.paused(session):
                text = "⏸ Партия на паузе\n\n" + text
            if owner:
                text += "\n\nКарточка для тебя; в группе её видят все."
            if page in {"overview", "scene"}:
                text += "\n\nБыстрые идеи — подсказки. Свой ход можно описать свободно."
        self._save()  # Persist nonce registration before publishing its keyboard.
        message_id = await self._deliver_card(bot, session, owner, text[:3900], markup)
        self._save()
        return message_id

    async def command(self, message):
        session = self.dnd.dnd_sessions.get(int(message.chat.id))
        if session is None:
            await message.answer("Активной партии нет. Начать: «упупа днд».")
            return
        async with self._lock(session.chat_id, 0):
            await self.show(message.bot, session)

    async def show_turn_cards(self, bot, session):
        """Refresh shared/player cards for the current participant input or roll."""
        state = getattr(session, "state", None)
        if getattr(session, "mode", None) != "participants" or state not in {"WAITING_ACTION", "WAITING_ROLL"}:
            return
        async with self._lock(session.chat_id, 0):
            await self.show(bot, session, 0, "overview")
        targets = (
            (getattr(session, "pending_roll", None) or {}).get("target_user_ids", [])
            if state == "WAITING_ROLL"
            else (getattr(session, "action_target_user_ids", None) or [])
        )
        targets = [int(value) for value in targets]
        if len(targets) == 1:
            owner = targets[0]
            async with self._lock(session.chat_id, owner):
                await self.show(bot, session, owner, "overview")

    def _can_act(self, session, actor, *, state="WAITING_ACTION"):
        if self.paused(session) or getattr(session, "state", None) != state:
            return False
        targets = (getattr(session, "pending_roll", None) or {}).get("target_user_ids", []) if state == "WAITING_ROLL" else getattr(session, "action_target_user_ids", [])
        return bool(self.dnd._can_user_act(session, actor, targets))

    def _valid_payload(self, session, actor, payload):
        kind = payload.get("kind")
        if kind in {"PAUSE", "RESUME", "RETRY"}:
            return self.dnd._user_is_host(session, actor) and (kind in {"RESUME", "PAUSE"} or not self.paused(session))
        if kind == "ROLL":
            return self._can_act(session, actor, state="WAITING_ROLL")
        if kind == "CHOOSE_ARCHETYPE":
            from AI.dnd_character_templates import ARCHETYPES

            return (getattr(session, "state", "") == "LOBBY" and str(actor) in _mapping(getattr(session, "participants", None))
                    and payload.get("choice") in ARCHETYPES and not self.paused(session))
        if not self._can_act(session, actor):
            return False
        snapshot = snapshot_session(session)
        if kind == "WAIT":
            return _wait_rule(snapshot) is not None
        if kind in {"USE_ITEM", "TRANSFER_ITEM"}:
            index = _int(payload.get("item_index"), -1)
            match = next((row for row_index, row in _visible_items(snapshot, actor) if row_index == index), None)
            if match is None or _item_ref(index, match).get("item_fingerprint") != payload.get("item_fingerprint"):
                return False
            if _int(_mapping(match).get("quantity"), 1) < 1:
                return False
            if kind == "TRANSFER_ITEM":
                target = _mapping(snapshot.get("participants")).get(str(payload.get("target_id")))
                return isinstance(target, dict) and _public(target) and target.get("active", True) and str(payload.get("target_id")) != str(actor)
        if kind == "ATTACK":
            target = _mapping(snapshot.get("enemy_combatants")).get(str(payload.get("target_id")))
            return isinstance(target, dict) and _public(target) and _int(target.get("hp"), 1) > 0 and target.get("status") not in {"DEAD", "DEFEATED"}
        if payload.get("object_id"):
            row = _mapping(snapshot.get("scene_objects")).get(str(payload["object_id"]))
            if not isinstance(row, dict) or not _public(row) or not row.get("available", True):
                return False
            rule_id = _mapping(payload.get("inputs")).get("rule_id")
            if rule_id:
                rule = _mapping(row.get("interactions")).get(str(rule_id))
                return _public_interaction(rule) is not None
            return True
        return True

    async def _input_prompt(self, callback, session, actor, *, object_id=None, item_name=None):
        if not self._can_act(session, actor):
            await callback.answer("Сейчас не твой свободный ход или партия на паузе.", show_alert=True)
            return
        identity = self.identity(session)
        # One stable input prompt per actor/window; navigation never reuses it.
        for message_id, row in session.menu_action_prompts.items():
            if row.get("actor_id") == actor and row.get("turn_id") == identity["turn_id"] and row.get("campaign_id") == identity["campaign_id"] and not row.get("closed") and row.get("phase") == identity["phase"]:
                await callback.answer("Ответь на уже открытое сообщение «Твой ход».", show_alert=True)
                return
        text = f"✍️ {_name(snapshot_session(session), actor)}, твой ход. Ответь на это сообщение своим действием."
        if object_id:
            row = _mapping(getattr(session, "scene_objects", None)).get(str(object_id))
            if not isinstance(row, dict) or not _public(row) or not row.get("available", True):
                await callback.answer("Объект больше недоступен.", show_alert=True)
                return
            text += f"\nБыстрая идея: взаимодействовать с {_text(row.get('name') or object_id, 90)}. Любая другая идея тоже допустима."
        if item_name:
            text += f"\nПредмет: {_text(item_name, 100)}. Опиши, как применяешь его, и выполни указанные условия предмета."
        sent = await callback.bot.send_message(session.chat_id, text, reply_markup=ForceReply(selective=True, input_field_placeholder="Действие твоего героя"), parse_mode=None)
        session.menu_action_prompts[str(sent.message_id)] = {**identity, "actor_id": actor, "object_id": object_id, "operation_id": secrets.token_hex(16), "closed": False}
        self._save()
        await callback.answer()

    async def _confirm(self, callback, session, actor, payload):
        if not self._valid_payload(session, actor, payload):
            await callback.answer("Действие сейчас недоступно. Обнови меню.", show_alert=True)
            return
        labels = {"ROLL": "Бросить кубик", "ATTACK": "Атаковать", "USE_ITEM": "Использовать предмет", "TRANSFER_ITEM": "Передать предмет", "OBJECT": "Взаимодействовать с объектом", "WAIT": "Осознанно подождать", "CHOOSE_ARCHETYPE": "Выбрать роль", "PAUSE": "Пауза", "RESUME": "Продолжить", "RETRY": "Повторить сохранённое продолжение"}
        description = labels.get(payload.get("kind"), "Действие")
        snapshot = snapshot_session(session)
        if payload.get("kind") == "WAIT":
            rule = _wait_rule(snapshot)
            description += ": " + _text(rule["label"], 100) + "\n" + _text(rule["success"]["text"], 300)
        if payload.get("kind") == "CHOOSE_ARCHETYPE":
            from AI.dnd_character_templates import stats_for_profile

            choice = payload["choice"]
            stats = stats_for_profile({"archetype": choice})
            description += ": " + choice.capitalize() + "\n" + ", ".join(f"{ability} {value}" for ability, value in stats.items())
        if payload.get("item_name"):
            description += ": " + _text(payload["item_name"], 100)
        if payload.get("kind") == "USE_ITEM":
            items = _mapping(snapshot.get("inventories")).get(str(actor)) or []
            index = _int(payload.get("item_index"), -1)
            item = _mapping(items[index]) if 0 <= index < len(items) else {}
            description += "\nВладелец: " + _name(snapshot, actor)
            if item.get("effect") or item.get("description"):
                description += "\nЭффект: " + _text(item.get("effect") or item.get("description"), 200)
            if item.get("charges_remaining") is not None:
                description += "\nЗаряды: " + str(item["charges_remaining"]) + "; расход — одно применение."
            cost_labels = {"DANGER_PLUS_1": "Опасность +1", "DROP_ITEM": "Предмет исчезнет", "GAIN_CONDITION": "Получишь состояние", "NEXT_ACCUSATION_SELF": "Следующее обвинение достанется владельцу"}
            if item.get("cost") in cost_labels:
                description += "\nЦена: " + cost_labels[item["cost"]]
        if payload.get("kind") == "TRANSFER_ITEM":
            description += " → " + _name(snapshot, payload.get("target_id"))
        if payload.get("kind") == "ATTACK":
            description += " → " + _text(_mapping(_mapping(snapshot.get("enemy_combatants")).get(str(payload.get("target_id")))).get("name"), 90)
            description += "\nОружие: " + _text(payload.get("item_id") or "без оружия", 100)
        if payload.get("kind") == "OBJECT":
            row = _mapping(_mapping(snapshot.get("scene_objects")).get(str(payload.get("object_id"))))
            rule = _public_interaction(_mapping(row.get("interactions")).get(str(_mapping(payload.get("inputs")).get("rule_id"))))
            description += ": " + _text(row.get("name"), 80) + " — " + _text(rule.get("label"), 100)
            if rule["uncertain"]:
                description += f"\nПроверка: {rule['ability']}, DC {rule['dc']}."
            description += "\nУспех: " + _outcome_preview(snapshot, rule["success"])
            if rule.get("success_with_cost"):
                description += "\nУспех с ценой: " + _outcome_preview(snapshot, rule["success_with_cost"])
                if rule["uncertain"]:
                    description += f" (результат {rule['dc']}–{rule['dc'] + rule['clean_success_margin'] - 1}; без цены — от {rule['dc'] + rule['clean_success_margin']})."
            if rule["uncertain"]:
                description += "\nЦена провала: " + _outcome_preview(snapshot, rule["failure"])
        request = {**payload, "operation_id": secrets.token_hex(16), "actor_id": actor}

        def markup(card, seq):
            return InlineKeyboardMarkup(inline_keyboard=[[
                self._button(session, actor, "✅ Подтвердить", "execute", card=card, seq=seq, **request),
                self._button(session, actor, "Отмена", "nav", card=card, seq=seq, page="overview"),
            ]])

        await self.show(callback.bot, session, actor, text=f"{description}\n\nДействие выполнится только после подтверждения.", build_markup=markup)
        await callback.answer()

    async def callback(self, callback):
        if not callback.message:
            await callback.answer("Сообщение меню недоступно.")
            return
        session = self.dnd.dnd_sessions.get(int(callback.message.chat.id))
        if session is None:
            await callback.answer("Партия уже закончилась.")
            return
        _ensure_ui(session)
        actor = int(callback.from_user.id)
        token_id = str(callback.data or "")[len(CALLBACK_PREFIX):]
        token = session.menu_ui_state["tokens"].get(token_id)
        if not isinstance(token, dict):
            await callback.answer("Кнопка устарела. Напиши «днд меню».", show_alert=True)
            return
        if token.get("owner_id") not in {0, actor}:
            await callback.answer("Это служебная карточка другого игрока.", show_alert=True)
            return
        async with self._lock(session.chat_id, actor):
            if self.dnd.dnd_sessions.get(session.chat_id) is not session:
                await callback.answer("Партия изменилась. Открой новое меню.")
                return
            operation = token.get("operation")
            payload = copy.deepcopy(_mapping(token.get("payload")))
            if operation == "nav":
                page = payload.get("page") if payload.get("page") in _PAGES else "overview"
                await self.show(callback.bot, session, actor, page)
                await callback.answer()
                return
            if operation == "object":
                row = _mapping(getattr(session, "scene_objects", None)).get(str(payload.get("object_id")))
                if not isinstance(row, dict) or not _public(row):
                    await callback.answer("Объект больше не находится в этой сцене.", show_alert=True)
                    return

                def object_markup(card, seq):
                    rows = [[self._button(session, actor, "✍️ Своя идея", "idea", card=card, seq=seq, object_id=str(payload.get("object_id")))]]
                    if row.get("available", True):
                        for rule_id, rule in list(_mapping(row.get("interactions")).items())[:5]:
                            rule = _public_interaction(rule)
                            if rule is not None:
                                rows.append([self._button(session, actor, _text(rule.get("label") or rule_id, 50), "confirm", card=card, seq=seq,
                                                         kind="OBJECT", object_id=str(payload.get("object_id")), inputs={"rule_id": str(rule_id)})])
                    rows.append([self._button(session, actor, "← Сцена", "nav", card=card, seq=seq, page="scene")])
                    return InlineKeyboardMarkup(inline_keyboard=rows)

                object_text = f"🔎 {_text(row.get('name') or payload.get('object_id'), 100)}\n{_text(row.get('state'), 200)}\n{_text(row.get('detail'), 300)}\n\nБыстрые идеи не ограничивают свой ход."
                await self.show(callback.bot, session, actor, text=object_text, build_markup=object_markup)
                await callback.answer()
                return
            card_seq = _int(session.menu_ui_state["seq"].get(token.get("card")))
            current_message = session.menu_ui_state["cards"].get(token.get("card"))
            if (not self._current(session, token) or card_seq != token.get("ui_seq")
                    or _int(current_message) != _int(getattr(callback.message, "message_id", None), -1)):
                await callback.answer("Ход или карточка изменились. Обнови меню.", show_alert=True)
                return
            if token.get("used"):
                await callback.answer("Это действие уже отправлено.")
                return
            if operation in {"compose", "idea", "item_idea"}:
                if operation == "item_idea" and not self._valid_payload(session, actor, payload):
                    await callback.answer("Предмет сейчас недоступен.", show_alert=True)
                    return
                await self._input_prompt(callback, session, actor, object_id=payload.get("object_id"), item_name=payload.get("item_name"))
            elif operation == "confirm":
                await self._confirm(callback, session, actor, payload)
            elif operation == "recipients":
                if not self._valid_payload(session, actor, {**payload, "kind": "USE_ITEM"}):
                    await callback.answer("Предмет сейчас недоступен.", show_alert=True)
                    return

                def markup(card, seq):
                    rows = []
                    for key, row in _mapping(getattr(session, "participants", None)).items():
                        if isinstance(row, dict) and _public(row) and row.get("active", True) and str(key) != str(actor):
                            rows.append([self._button(session, actor, _name(snapshot_session(session), key), "confirm", card=card, seq=seq, **{**payload, "kind": "TRANSFER_ITEM", "target_id": int(key), "quantity": 1})])
                    rows.append([self._button(session, actor, "Отмена", "nav", card=card, seq=seq, page="items")])
                    return InlineKeyboardMarkup(inline_keyboard=rows)

                await self.show(callback.bot, session, actor, text="Кому передать одну вещь?", build_markup=markup)
                await callback.answer()
            elif operation == "execute":
                if not self.execute_action or not self._valid_payload(session, actor, payload):
                    await callback.answer("Действие сейчас недоступно. Обнови меню.", show_alert=True)
                    return
                token["used"] = True
                self._save()
                await callback.answer("Действие принято.")
                request = {**payload, **self.identity(session), "actor_id": actor}
                await self.execute_action(callback.bot, session.chat_id, actor, request)
                if self.dnd.dnd_sessions.get(session.chat_id) is session:
                    await self.show(callback.bot, session, actor, "overview")

    async def handle_message(self, event):
        text = " ".join(str(getattr(event, "text", None) or "").strip().casefold().split())
        if text in MENU_COMMANDS:
            await self.command(event)
            return True
        chat, user, reply = getattr(event, "chat", None), getattr(event, "from_user", None), getattr(event, "reply_to_message", None)
        if chat is None or user is None or reply is None:
            return False
        session = self.dnd.dnd_sessions.get(int(chat.id))
        if session is None:
            return False
        prompt = _mapping(getattr(session, "menu_action_prompts", None)).get(str(reply.message_id))
        if prompt is not None:
            async with self._lock(chat.id, user.id):
                if not self._can_act(session, int(user.id)) or prompt.get("actor_id") != int(user.id) or not self._window_current(session, prompt) or prompt.get("closed"):
                    await event.answer("Этот ввод уже закрыт или адресован другому герою. Открой «днд меню».")
                    return True
                action = str(getattr(event, "text", None) or getattr(event, "caption", None) or "").strip()
                if not action:
                    await event.answer("Опиши действие своего героя текстом.")
                    return True
                if len(action) > 3000:
                    await event.answer("Сократи заявку до 3000 символов.")
                    return True
                message_id = getattr(event, "message_id", None)
                operation_id = hashlib.sha256(f"{prompt['operation_id']}:{message_id}:{action}".encode("utf-8")).hexdigest()[:32]
                request = {**self.identity(session), "kind": "FREE_TEXT", "actor_id": int(user.id), "text": action, "operation_id": operation_id}
                if self.execute_action:
                    await self.execute_action(event.bot, chat.id, int(user.id), request)
                else:
                    await self.dnd.handle_free_action(event)
                return True
        if str(reply.message_id) in {str(value) for value in (getattr(session, "menu_info_message_ids", None) or [])}:
            await event.answer("Это меню, а не заявка. Нажми «✍️ Свой ход» и ответь на сообщение ввода.")
            return True
        return False


class DndMenuMiddleware(BaseMiddleware):
    def __init__(self, service):
        self.service = service

    async def __call__(self, handler, event, data):
        if await self.service.handle_message(event):
            return None
        return await handler(event, data)


def configure_dnd_menu(dnd, router, *, state_policy=None, execute_action=None, identity_for=None):
    existing = getattr(router, "_upupa_dnd_menu_service", None)
    if existing is not None:
        if execute_action:
            existing.execute_action = execute_action
        return existing
    service = DndMenuService(dnd, execute_action=execute_action, identity_for=identity_for)
    if state_policy:
        state_policy.add_ensure_hook(_ensure_ui)
        for field in ("menu_ui_state", "menu_info_message_ids", "menu_action_prompts"):
            state_policy.add_state_field(field, lambda session, name=field: copy.deepcopy(getattr(session, name, {} if name != "menu_info_message_ids" else [])))
        state_policy.add_restore_hook(lambda session, _data: _ensure_ui(session))
    router.message.outer_middleware(DndMenuMiddleware(service))
    router.callback_query.register(service.callback, F.data.startswith(CALLBACK_PREFIX))
    router._upupa_dnd_menu_service = service
    return service
