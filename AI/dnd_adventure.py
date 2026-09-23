"""Adventure rules and commands called explicitly by campaign and completion.

This module installs no wrappers. Character presence is a roster flag so leaving
never destroys a hero, their possessions, or the final adventure report.
"""
from __future__ import annotations

import re
from collections import Counter


_MISSION = re.compile(r"\[MISSION:(SUCCESS|FAILURE);EVIDENCE:([^\]]+)\]", re.I)


def message_chunks(text: str, limit: int = 1900) -> list[str]:
    """Preserve complete text; 1900 Unicode scalars also fit Telegram in UTF-16."""
    remaining = str(text or "")
    chunks = []
    while len(remaining) > limit:
        split = remaining.rfind("\n", 0, limit)
        if split < limit // 2:
            split = remaining.rfind(" ", 0, limit)
        split = split + 1 if split >= limit // 2 else limit
        chunks.append(remaining[:split])
        remaining = remaining[split:]
    if remaining:
        chunks.append(remaining)
    return chunks


def is_dead(sheet) -> bool:
    if not isinstance(sheet, dict) or not sheet:
        return False
    try:
        hp = int(sheet.get("hp", 1))
    except (TypeError, ValueError):
        hp = 1
    return sheet.get("status") == "dead" or hp <= 0


def adventure_context(session) -> str:
    length = getattr(session, "adventure_length", "short")
    budget = "8–12" if length == "short" else "24–36"
    goal = getattr(session, "mission_goal", "") or "Сформулируй ясную достижимую цель в первой сцене."
    absent = [p.get("name", str(uid)) for uid, p in
              (getattr(session, "participants", {}) or {}).items() if not p.get("active", True)]
    return (
        f"ПАРАМЕТРЫ ПРИКЛЮЧЕНИЯ: {'короткое' if length == 'short' else 'длинное'}, ориентир {budget} сцен. "
        f"Сейчас сцена {getattr(session, 'scene_count', 0)}. Это темп, не автоматическая победа по таймеру.\n"
        f"ЦЕЛЬ МИССИИ: {goal}\n"
        "Не меняй указанную цель. При завершении явно напиши, достигнута ли она, и краткий итог. "
        "Добавь [MISSION:SUCCESS;EVIDENCE:конкретный факт достижения] либо "
        "[MISSION:FAILURE;EVIDENCE:причина провала]. END не означает победу. "
        "Не придумывай достижения, смерть или добычу для красивого финала.\n"
        "ОТСУТСТВУЮТ: " + (", ".join(absent) or "никто") + ". "
        "Отсутствующие не принимают решений, не получают ход, урон или броски.\n"
        "Разреши именно последнюю заявку её автора; старые действия не подменяют её. "
        "Не назначай бросок для очевидного или невозможного действия либо при отсутствии цены провала."
    )


def apply_mission_result(session, text: str) -> str:
    source = str(text or "")
    if re.search(r"\[ACTION:END\]", source, re.I):
        match = _MISSION.search(source)
        if match:
            session.mission_outcome = match[1].lower()
            session.mission_evidence = match[2].strip()[:800]
    return _MISSION.sub("", source)


def _items(items) -> Counter:
    result = Counter()
    for item in items or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "предмет")
            try:
                quantity = max(1, int(item.get("qty", item.get("quantity", 1)) or 1))
            except (TypeError, ValueError):
                quantity = 1
        else:
            name, quantity = str(item), 1
        result[name] += quantity
    return result


def adventure_report(session) -> str:
    outcome = getattr(session, "mission_outcome", "pending")
    status = "достигнута" if outcome == "success" else "не достигнута"
    evidence = getattr(session, "mission_evidence", "") or "Достижение цели не зафиксировано."
    lines = [f"🏁 Итог миссии: цель {status}.",
             "Цель: " + (getattr(session, "mission_goal", "") or "см. завязку приключения"),
             "Краткий итог: " + evidence]
    dead = []
    loot = []
    for key, player in (getattr(session, "participants", {}) or {}).items():
        name = player.get("name") or str(key)
        sheet = (getattr(session, "character_sheets", {}) or {}).get(key, {})
        if is_dead(sheet):
            dead.append(name)
            loot.append(f"{name}: погиб, вещи больше недоступны")
            continue
        current = _items((getattr(session, "inventories", {}) or {}).get(key))
        initial = _items((getattr(session, "initial_inventories", {}) or {}).get(key))
        gained = current - initial
        loot.append(f"{name}: " + (", ".join(f"{n} ×{q}" for n, q in gained.items()) or "новых вещей не осталось"))
    lines.append("Погибли: " + (", ".join(dead) or "никто"))
    lines.append("Получено и сохранено к финалу:\n" + ("\n".join(loot) or "нет участников"))
    return "\n".join(lines)


async def handle_adventure_message(dnd, bot, event, policy) -> bool:
    chat = getattr(event, "chat", None)
    user = getattr(event, "from_user", None)
    if chat is None or user is None:
        return False
    session = dnd.dnd_sessions.get(chat.id)
    if session is None:
        return False
    text = str(getattr(event, "text", None) or "").strip()
    reply = getattr(event, "reply_to_message", None)
    prompt_id = getattr(session, "setup_prompt_message_id", None)
    if (session.state == "WAITING_PLOT" and prompt_id and reply
            and reply.message_id == prompt_id and dnd._user_is_host(session, user.id)):
        if not text or len(text) > 1200:
            await bot.send_message(chat.id, "Укажи цель текстом до 1200 символов.")
            return True
        session.mission_goal = "" if text.casefold() == "без цели" else text
        session.setup_prompt_message_id = None
        dnd.persist_dnd_sessions()
        await bot.send_message(chat.id, "🎯 Цель сохранена. Теперь выбери сюжет в меню выше.")
        return True
    command = text.casefold()
    if session.mode != "participants" or command not in {"захожу", "ушел", "ушёл", "ушла"}:
        return False
    if session.state in {"ENDING", "FINISHED"}:
        return False
    key = str(user.id)
    player = session.participants.get(key)
    if command == "захожу":
        if player and player.get("active", True):
            await bot.send_message(chat.id, "Ты уже в партии.")
            return True
        sheet = (getattr(session, "character_sheets", {}) or {}).get(key, {})
        if is_dead(sheet):
            await bot.send_message(chat.id, "Этот герой погиб. Новый герой создаётся в следующей партии.")
            return True
        first_join = player is None
        session.participants[key] = dict(player or {}, user_id=user.id, name=user.first_name, active=True)
        dnd.persist_dnd_sessions()
        if first_join and policy.after_participant_joined:
            await policy.after_participant_joined(dnd, bot, event, session, user.id, user.first_name)
            from copy import deepcopy
            session.initial_inventories.setdefault(key, deepcopy(session.inventories.get(key, [])))
        if session.state == "WAITING_ACTION" and not getattr(session, "action_prompt_message_id", None):
            await dnd.open_action_window(bot, chat.id, target_user_ids=[user.id])
        await bot.send_message(chat.id, f"{user.first_name} присоединился к партии. Текущую заявку другого героя не меняем.")
    else:
        if player is None or not player.get("active", True):
            await bot.send_message(chat.id, "Ты сейчас не участвуешь.")
            return True
        if session.state == "RESOLVING":
            # A reserved action remains durable; absence applies to future turns.
            await bot.send_message(chat.id, "Уход учтён. Уже заявленное действие мастер ещё разрешит.")
        player["active"] = False
        (getattr(session, "pending_actions", {}) or {}).pop(key, None)
        targets = list(getattr(session, "action_target_user_ids", []) or [])
        if user.id in targets and session.state == "WAITING_ACTION":
            remaining = [uid for uid in targets if uid != user.id]
            active = sorted(dnd._participant_ids(session))
            session.action_target_user_ids = remaining or active[:1]
            dnd.persist_dnd_sessions()
            # Keep other participants' submitted actions in a shared window.
            # Opening a new window would clear them before auto-finalization.
            if not remaining and active:
                await dnd.open_action_window(bot, chat.id, target_user_ids=session.action_target_user_ids)
        pending_roll = getattr(session, "pending_roll", None)
        if isinstance(pending_roll, dict) and user.id in (pending_roll.get("target_user_ids") or []):
            # Do not roll on behalf of an absent PC or retarget their action.
            session.pending_roll = None
            session.state = "WAITING_ACTION"
            active = sorted(dnd._participant_ids(session))
            if active:
                await dnd.open_action_window(bot, chat.id, target_user_ids=active[:1])
        if not dnd._participant_ids(session) and session.state == "WAITING_ACTION":
            session.action_prompt_message_id = None
            session.action_deadline = None
        await bot.send_message(chat.id, f"{user.first_name} вышел из сцены. Герой и инвентарь сохранены. Вернуться: «захожу».")
    dnd.persist_dnd_sessions()
    return True
