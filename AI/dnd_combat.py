"""Combat layer for participant-mode DnD: abilities, HP/AC, enemy attacks and one emergency heal."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re

from aiogram import BaseMiddleware


COMBAT_MARKER = "БОЕВАЯ МЕХАНИКА УПУПЫ"
ABILITY_KEYS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
ABILITY_LABELS = {
    "STR": "Сила",
    "DEX": "Ловкость",
    "CON": "Телосложение",
    "INT": "Интеллект",
    "WIS": "Мудрость",
    "CHA": "Харизма",
}
ABILITY_SHORT = {
    "STR": "СИЛ",
    "DEX": "ЛОВ",
    "CON": "ТЕЛ",
    "INT": "ИНТ",
    "WIS": "МДР",
    "CHA": "ХАР",
}
STANDARD_ARRAY = (16, 14, 13, 12, 10, 8)
SKILL_ABILITIES = {
    "Акробатика": "DEX",
    "Атлетика": "STR",
    "Внимательность": "WIS",
    "Выживание": "WIS",
    "Дрессировка": "WIS",
    "Запугивание": "CHA",
    "Исполнение": "CHA",
    "История": "INT",
    "Ловкость рук": "DEX",
    "Магия": "INT",
    "Медицина": "WIS",
    "Обман": "CHA",
    "Природа": "INT",
    "Проницательность": "WIS",
    "Расследование": "INT",
    "Религия": "INT",
    "Скрытность": "DEX",
    "Убеждение": "CHA",
}
ENEMY_POWER = {
    "LOW": (1, 4),
    "MEDIUM": (2, 6),
    "HIGH": (3, 8),
    "DEADLY": (4, 10),
}
_ACTION_ATTACK_RE = re.compile(r"\[ACTION:ENEMY_ATTACK;(.*?)\]", re.I | re.S)
_ACTION_ROLL_RE = re.compile(r"\[ACTION:ROLL;(.*?)\]", re.I | re.S)
_ABILITY_RE = re.compile(r"(?:^|;)ABILITY:(STR|DEX|CON|INT|WIS|CHA)(?:;|$)", re.I)

COMBAT_RULES = f"""{COMBAT_MARKER}.
В режиме участников у каждого героя есть шесть характеристик, HP и КБ. Это реальные механики, а не декор.
Для ACTION:ROLL указывай ABILITY:STR/DEX/CON/INT/WIS/CHA. Для CHECK выбирай характеристику по смыслу навыка и действия; для SAVE — по типу опасности.
Код сам прибавит модификатор характеристики к d20. Не подгоняй DC под конкретного героя и не вписывай бонус вручную.

Если NPC или монстр непосредственно атакует героя и может снять здоровье, вместо SAVE используй:
[ACTION:ENEMY_ATTACK;TARGETS:12345;POWER:MEDIUM;REASON:орк рубит Алису тесаком]
TARGETS — ровно один живой герой. POWER: LOW для мелкой угрозы, MEDIUM для обычного противника, HIGH для опасного, DEADLY только для редкой боссовой атаки.
Код сам бросит d20 против КБ, посчитает урон и HP. Никогда не сообщай заранее, попала атака или нет — это решит код.
Натуральная 1 у врага всегда промах, натуральная 20 — попадание и критический урон.
При 0 HP герой погибает и больше не участвует в ходах. В партии ровно одна одноразовая аварийная лечилка: она заранее случайно лежит у одного героя и автоматически спасает первого героя, который упал бы до 0 HP, после чего исчезает.
Не создавай вторую лечилку и не лечи HP повествовательно без явной игровой причины.
""".strip()


def ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def _valid_stats(stats) -> bool:
    if not isinstance(stats, dict):
        return False
    try:
        values = [int(stats[key]) for key in ABILITY_KEYS]
    except (KeyError, TypeError, ValueError):
        return False
    return sorted(values) == sorted(STANDARD_ARRAY)


def _random_stats() -> dict[str, int]:
    values = random.sample(list(STANDARD_ARRAY), k=len(STANDARD_ARRAY))
    return dict(zip(ABILITY_KEYS, values))


def _build_sheet(stats: dict[str, int]) -> dict:
    clean = {key: int(stats[key]) for key in ABILITY_KEYS}
    con_mod = ability_modifier(clean["CON"])
    dex_mod = ability_modifier(clean["DEX"])
    max_hp = max(8, 12 + 2 * con_mod)
    ac = max(9, min(16, 11 + dex_mod))
    return {
        "stats": clean,
        "hp": max_hp,
        "max_hp": max_hp,
        "ac": ac,
        "status": "alive",
    }


def _alive(sheet) -> bool:
    if not isinstance(sheet, dict):
        return False
    try:
        hp = int(sheet.get("hp", 0))
    except (TypeError, ValueError):
        hp = 0
    return hp > 0 and str(sheet.get("status") or "alive") != "dead"


def _participant_name(session, user_id: int) -> str:
    item = (getattr(session, "participants", {}) or {}).get(str(int(user_id)), {})
    return item.get("name") or f"Егрок {int(user_id)}"


def _stats_line(stats: dict) -> str:
    chunks = []
    for key in ABILITY_KEYS:
        score = int(stats[key])
        mod = ability_modifier(score)
        chunks.append(f"{ABILITY_SHORT[key]} {score} ({mod:+d})")
    return " · ".join(chunks)


def _strip_fence(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I | re.S).strip()


def _parse_stats_payload(raw, user_ids: list[int]) -> dict[str, dict[str, int]]:
    try:
        data = json.loads(_strip_fence(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for user_id in user_ids:
        key = str(int(user_id))
        stats = data.get(key)
        if _valid_stats(stats):
            result[key] = {ability: int(stats[ability]) for ability in ABILITY_KEYS}
    return result


async def _generate_stats(dnd, session, user_ids: list[int]) -> dict[str, dict[str, int]]:
    if not user_ids:
        return {}
    from AI import dnd_campaign as campaign

    lines = []
    profiles = getattr(session, "character_profiles", {}) or {}
    for user_id in user_ids:
        key = str(int(user_id))
        profile = profiles.get(key) or {}
        lines.append(
            f"- ID {key} {_participant_name(session, user_id)}: "
            f"образ={profile.get('style')}; сила={profile.get('strength')}; "
            f"слабость={profile.get('weakness')}; приём={profile.get('special')}"
        )
    prompt = (
        "Служебно распредели характеристики героям по их профилям. Это не игровой ход, без ACTION-тегов.\n"
        "Для КАЖДОГО героя используй ровно один и тот же набор значений 16,14,13,12,10,8, каждое число ровно один раз. "
        "Назначь их осмысленно: сила STR, ловкость DEX, телосложение CON, интеллект INT, мудрость WIS, харизма CHA. "
        "Сильные/слабые стороны и образ должны влиять на распределение.\n"
        + "\n".join(lines)
        + "\nВерни только JSON-объект вида {\"123\":{\"STR\":16,\"DEX\":14,\"CON\":13,\"INT\":12,\"WIS\":10,\"CHA\":8}}."
    )
    try:
        raw = await campaign._ephemeral_generate(dnd, session, prompt)
        parsed = _parse_stats_payload(raw, user_ids)
    except Exception:
        logging.exception("DnD combat stat generation failed chat_id=%s", getattr(session, "chat_id", None))
        parsed = {}
    for user_id in user_ids:
        key = str(int(user_id))
        parsed.setdefault(key, _random_stats())
    return parsed


def _history_stats(campaign, session, user_id: int):
    old = campaign._player_history(session.chat_id, user_id) or {}
    old_profile = old.get("profile") or {}
    current_profile = (getattr(session, "character_profiles", {}) or {}).get(str(int(user_id))) or {}
    sheet = old.get("sheet") or {}
    stats = sheet.get("stats") if isinstance(sheet, dict) else None
    if old_profile == current_profile and _valid_stats(stats):
        return {key: int(stats[key]) for key in ABILITY_KEYS}
    return None


async def initialize_party_combat(dnd, bot, session) -> None:
    from AI import dnd_campaign as campaign

    campaign._ensure(session)
    participants = list((getattr(session, "participants", {}) or {}).values())
    user_ids = [int(item["user_id"]) for item in participants if item.get("user_id") is not None]
    if not user_ids:
        session.character_sheets = {}
        session.healing_charge = {"owner_id": None, "used": True}
        return

    stats_by_user = {}
    missing = []
    for user_id in user_ids:
        inherited = _history_stats(campaign, session, user_id)
        if inherited:
            stats_by_user[str(user_id)] = inherited
        else:
            missing.append(user_id)
    stats_by_user.update(await _generate_stats(dnd, session, missing))

    session.character_sheets = {
        str(user_id): _build_sheet(stats_by_user.get(str(user_id), _random_stats()))
        for user_id in user_ids
    }
    owner_id = random.choice(user_ids)
    session.healing_charge = {"owner_id": owner_id, "used": False}
    dnd.persist_dnd_sessions()

    lines = ["⚔️ Боевые параметры:"]
    for user_id in user_ids:
        sheet = session.character_sheets[str(user_id)]
        lines.append(
            f"• {_participant_name(session, user_id)} — ❤️ {sheet['hp']}/{sheet['max_hp']}, "
            f"🛡 КБ {sheet['ac']}; {_stats_line(sheet['stats'])}"
        )
    lines.append(
        f"🧪 Одноразовая аварийная лечилка случайно досталась {_participant_name(session, owner_id)}. "
        "Она автоматически спасёт первого героя, который свалится до 0 HP."
    )
    await bot.send_message(session.chat_id, "\n".join(lines))


def _combat_context(session) -> str:
    sheets = getattr(session, "character_sheets", {}) or {}
    if not sheets:
        return COMBAT_RULES
    lines = []
    for key, sheet in sheets.items():
        if not isinstance(sheet, dict) or not isinstance(sheet.get("stats"), dict):
            continue
        name = _participant_name(session, int(key))
        status = "жив" if _alive(sheet) else "погиб"
        lines.append(
            f"- ID {key} {name}: HP {sheet.get('hp', 0)}/{sheet.get('max_hp', 0)}, КБ {sheet.get('ac')}, "
            f"{_stats_line(sheet['stats'])}; статус={status}"
        )
    charge = getattr(session, "healing_charge", {}) or {}
    owner_id = charge.get("owner_id")
    heal_text = "использована" if charge.get("used") else (
        f"у ID {owner_id} ({_participant_name(session, int(owner_id))})" if owner_id is not None else "нет"
    )
    return COMBAT_RULES + "\nБОЕВОЕ СОСТОЯНИЕ:\n" + ("\n".join(lines) or "- нет") + f"\nЛЕЧИЛКА: {heal_text}."


def _extract_roll_ability(text: str) -> str | None:
    match = _ACTION_ROLL_RE.search(str(text or ""))
    if not match:
        return None
    ability = _ABILITY_RE.search(";" + match.group(1).strip(";") + ";")
    return ability.group(1).upper() if ability else None


def _infer_save_ability(reason: str) -> str:
    text = str(reason or "").casefold()
    if any(word in text for word in ("яд", "отрав", "болез", "мороз", "жар", "удуш", "выдерж", "истощ", "боль")):
        return "CON"
    if any(word in text for word in ("страх", "очар", "разум", "гипноз", "волю", "иллюз", "паник")):
        return "WIS"
    if any(word in text for word in ("уверн", "отскоч", "пад", "равновес", "взрыв", "обвал", "ловуш", "уклон")):
        return "DEX"
    return "CON"


def _ability_for_roll(roll: dict) -> str | None:
    explicit = str(roll.get("ability") or "").upper()
    if explicit in ABILITY_KEYS:
        return explicit
    if str(roll.get("type") or "CHECK").upper() == "CHECK":
        return SKILL_ABILITIES.get(roll.get("skill"))
    return _infer_save_ability(roll.get("reason") or "")


def _living_ids(session) -> set[int]:
    sheets = getattr(session, "character_sheets", {}) or {}
    if not sheets:
        return {int(value["user_id"]) for value in (getattr(session, "participants", {}) or {}).values()}
    result = set()
    for key, sheet in sheets.items():
        if _alive(sheet):
            try:
                result.add(int(key))
            except (TypeError, ValueError):
                continue
    return result


def _parse_attack(text: str) -> dict | None:
    match = _ACTION_ATTACK_RE.search(str(text or ""))
    if not match:
        return None
    fields = {}
    for part in match.group(1).split(";"):
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    targets = []
    for raw in fields.get("TARGETS", "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            targets.append(int(raw))
    power = fields.get("POWER", "MEDIUM").upper()
    if power not in ENEMY_POWER:
        power = "MEDIUM"
    return {
        "target_user_id": targets[0] if targets else None,
        "power": power,
        "reason": fields.get("REASON") or "враг атакует",
    }


def _consume_heal(session, target_id: int) -> str | None:
    charge = getattr(session, "healing_charge", {}) or {}
    if charge.get("used") or charge.get("owner_id") is None:
        return None
    sheets = getattr(session, "character_sheets", {}) or {}
    sheet = sheets.get(str(int(target_id)))
    if not isinstance(sheet, dict):
        return None
    heal = random.randint(1, 8) + 2
    restored = max(1, min(int(sheet.get("max_hp", heal)), heal))
    sheet["hp"] = restored
    sheet["status"] = "alive"
    charge["used"] = True
    session.healing_charge = charge
    owner_id = int(charge["owner_id"])
    return (
        f"🧪 {_participant_name(session, owner_id)} оказался тем самым типом с одноразовой лечилкой: "
        f"{_participant_name(session, target_id)} возвращается с {restored} HP. Лечилка всё, пиздец ей."
    )


def _resolve_enemy_attack(session, attack: dict) -> tuple[str, str, bool]:
    living = sorted(_living_ids(session))
    if not living:
        return "☠️ Атаковать уже некого.", "Вся партия уже погибла. Заверши историю через [ACTION:END].", True
    target_id = attack.get("target_user_id")
    if target_id not in living:
        target_id = random.choice(living)
    sheet = session.character_sheets[str(int(target_id))]
    power = attack["power"]
    bonus, sides = ENEMY_POWER[power]
    natural = random.randint(1, 20)
    total = natural + bonus
    ac = int(sheet["ac"])
    hit = natural == 20 or (natural != 1 and total >= ac)
    name = _participant_name(session, target_id)
    reason = attack.get("reason") or "враг атакует"
    if not hit:
        summary = f"👹 {reason}. Бросок врага: {natural} + {bonus} = {total} против КБ {ac} — мимо."
        prompt = (
            f"Враг атаковал {name}: {reason}. Бросок атаки {natural}+{bonus}={total} против КБ {ac}: промах. "
            "Продолжай сюжет, учитывая промах."
        )
        return summary, prompt, False

    dice_count = 2 if natural == 20 else 1
    damage_rolls = [random.randint(1, sides) for _ in range(dice_count)]
    damage = sum(damage_rolls)
    old_hp = int(sheet.get("hp", 0))
    sheet["hp"] = max(0, old_hp - damage)
    crit = " КРИТ." if natural == 20 else ""
    summary = (
        f"👹 {reason}. Бросок врага: {natural} + {bonus} = {total} против КБ {ac} — попал.{crit}\n"
        f"💥 Урон: {damage}. ❤️ {name}: {sheet['hp']}/{sheet['max_hp']}."
    )
    heal_note = None
    died = False
    if int(sheet["hp"]) <= 0:
        heal_note = _consume_heal(session, target_id)
        if heal_note:
            summary += "\n" + heal_note
        else:
            sheet["hp"] = 0
            sheet["status"] = "dead"
            died = True
            summary += f"\n☠️ {name} падает до 0 HP и выбывает из этой егры."
    final_hp = int(sheet.get("hp", 0))
    prompt = (
        f"Враг атаковал {name}: {reason}. Бросок {natural}+{bonus}={total} против КБ {ac}: попадание. "
        f"Урон {damage}; HP было {old_hp}, стало {final_hp}. "
    )
    if heal_note:
        prompt += "Одноразовая аварийная лечилка автоматически сработала и теперь израсходована. "
    elif died:
        prompt += f"{name} погиб и больше не должен получать игровые ходы. "
    if natural == 20:
        prompt += "Это была натуральная 20 и критический урон. "
    prompt += "Продолжай сюжет с этим точным исходом."
    all_dead = not _living_ids(session)
    if all_dead:
        prompt += " Вся партия погибла: дай короткий финал и закончи [ACTION:END]."
    return summary, prompt, all_dead


async def _resolve_player_roll(dnd, message, session) -> None:
    roll = session.pending_roll or {
        "type": "CHECK",
        "skill": None,
        "reason": "проверка по ситуации",
        "dc": None,
        "mode": "NORMAL",
        "target_user_ids": [],
    }
    user_id = int(message.from_user.id)
    if not dnd._can_user_act(session, user_id, roll.get("target_user_ids") or []):
        await message.answer("Этот бросок не твой. Или ты уже героически помер.")
        return

    rolls, natural_result = dnd._roll_d20(roll.get("mode", "NORMAL"))
    roll_type = roll.get("type", "CHECK")
    skill = dnd._normalize_roll_skill(roll.get("skill")) if roll_type == "CHECK" else None
    reason = roll.get("reason") or "проверка по ситуации"
    dc = roll.get("dc")
    mode = roll.get("mode", "NORMAL")
    ability = _ability_for_roll(roll)
    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(user_id)) or {}
    stats = sheet.get("stats") if isinstance(sheet, dict) else {}
    score = int(stats.get(ability, 10)) if ability else 10
    modifier = ability_modifier(score) if ability else 0
    result = natural_result + modifier
    outcome = dnd._roll_outcome(result, dc)
    natural_note = dnd._natural_roll_note(natural_result)

    session.state = "RESOLVING"
    session.pending_roll = None
    dnd.persist_dnd_sessions()

    roll_label = dnd._roll_type_label(roll_type, skill)
    result_lines = [f"🎲 {message.from_user.first_name}: {roll_label} — {reason}"]
    if dc is not None:
        difficulty_line = f"⚙️ Сложность — {dc}"
        if mode == "ADVANTAGE":
            difficulty_line += " (с преимуществом)"
        elif mode == "DISADVANTAGE":
            difficulty_line += " (с помехой)"
        result_lines.append(difficulty_line + ".")
    elif mode == "ADVANTAGE":
        result_lines.append("⚙️ Бросок с преимуществом.")
    elif mode == "DISADVANTAGE":
        result_lines.append("⚙️ Бросок с помехой.")

    if len(rolls) == 1:
        roll_text = f"🎯 Кубик — {natural_result}"
    else:
        roll_text = f"🎯 Кубики — {rolls[0]} и {rolls[1]}, выбран {natural_result}"
    if ability:
        roll_text += f"; {ABILITY_LABELS[ability]} {modifier:+d} → итог {result}"
    else:
        roll_text += f" → итог {result}"
    if natural_note:
        roll_text += f" ({natural_note})"
    result_lines.append(roll_text)
    await message.answer("\n".join(result_lines))

    if roll_type == "SAVE":
        prompt_roll_label = "спасбросок"
    elif skill:
        prompt_roll_label = f"проверку навыка «{skill}»"
    else:
        prompt_roll_label = "проверку"
    prompt_parts = [
        f"Игрок {message.from_user.first_name} сделал {prompt_roll_label}: {reason}.",
        f"Режим: {dnd._roll_mode_label(mode)}.",
        f"Броски d20: {rolls}; выбранное значение: {natural_result}.",
    ]
    if ability:
        prompt_parts.append(
            f"Характеристика: {ABILITY_LABELS[ability]} ({ability}) {score}, модификатор {modifier:+d}; итог: {result}."
        )
    else:
        prompt_parts.append(f"Модификатор не применялся; итог: {result}.")
    if dc is not None:
        prompt_parts.append(f"Сложность: {dc}; результат: {outcome}.")
    else:
        prompt_parts.append("Сложность не была задана; трактуй итог по ситуации.")
    if natural_note:
        prompt_parts.append(
            f"На d20 выпала {natural_note}; отметь это в описании, но не меняй автоматически исход против сложности."
        )
    prompt_parts.append("Продолжай сюжет.")

    try:
        response_text = await dnd.generate_session_response(
            session,
            dnd.with_scene_direction(session, " ".join(prompt_parts)),
        )
        await dnd.parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD combat roll continuation failed chat_id=%s", message.chat.id)
        await message.answer("Мастер завис, но егра сохранена.")
        await dnd.open_action_window(message.bot, message.chat.id)


class DndCombatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from AI import dnd

        text = str(getattr(event, "text", None) or "")
        if "кидаю" not in text.casefold():
            return await handler(event, data)
        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is None or user is None:
            return await handler(event, data)
        session = dnd.dnd_sessions.get(int(chat.id))
        if (
            not session
            or session.state != "WAITING_ROLL"
            or not dnd._is_participant_mode(session)
            or not (getattr(session, "character_sheets", {}) or {})
        ):
            return await handler(event, data)
        await _resolve_player_roll(dnd, event, session)
        return None


def _active_sheet(session, user_id: int):
    return (getattr(session, "character_sheets", {}) or {}).get(str(int(user_id)))


def _hero_combat_lines(session, user_id: int) -> list[str]:
    sheet = _active_sheet(session, user_id)
    if not isinstance(sheet, dict) or not _valid_stats(sheet.get("stats")):
        return []
    status = "жив" if _alive(sheet) else "погиб"
    lines = [
        f"❤️ Здоровье: {sheet.get('hp', 0)}/{sheet.get('max_hp', 0)} ({status})",
        f"🛡 Класс брони: {sheet.get('ac')}",
        "📊 " + _stats_line(sheet["stats"]),
    ]
    charge = getattr(session, "healing_charge", {}) or {}
    if not charge.get("used") and charge.get("owner_id") is not None and int(charge["owner_id"]) == int(user_id):
        lines.append("🧪 Одноразовая аварийная лечилка: при тебе, ещё целая.")
    return lines


def install_dnd_combat(dnd_router) -> None:
    if getattr(dnd_router, "_upupa_dnd_combat_configured", False):
        return

    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands
    from AI.dnd_completion import DndParticipantCompletionMiddleware

    original_ensure = campaign._ensure

    def ensure(session):
        original_ensure(session)
        if not isinstance(getattr(session, "character_sheets", None), dict):
            session.character_sheets = {}
        if not isinstance(getattr(session, "healing_charge", None), dict):
            session.healing_charge = {"owner_id": None, "used": True}

    campaign._ensure = ensure

    original_state = campaign._state

    def state(session):
        row = original_state(session)
        ensure(session)
        row["character_sheets"] = session.character_sheets
        row["healing_charge"] = session.healing_charge
        return row

    campaign._state = state

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + "\n" + _combat_context(session)

    campaign._campaign_context = campaign_context

    if COMBAT_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + COMBAT_RULES

    original_start_story = campaign._start_story

    async def start_story(dnd_module, bot, session, plot, continuation=False, message=None):
        await initialize_party_combat(dnd_module, bot, session)
        return await original_start_story(dnd_module, bot, session, plot, continuation, message)

    campaign._start_story = start_story

    original_auto_profile = campaign._auto_profile

    async def auto_profile(dnd_module, session, user_id):
        profile = await original_auto_profile(dnd_module, session, user_id)
        ensure(session)
        stats = await _generate_stats(dnd_module, session, [int(user_id)])
        session.character_sheets[str(int(user_id))] = _build_sheet(stats[str(int(user_id))])
        dnd_module.persist_dnd_sessions()
        return profile

    campaign._auto_profile = auto_profile

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        original_archive(dnd_module, session, finale, epilogue)
        chat = campaign._chat_history(session.chat_id, True)
        campaigns = chat.get("campaigns") or []
        if campaigns:
            campaigns[-1]["character_sheets"] = dict(getattr(session, "character_sheets", {}) or {})
        players = chat.setdefault("players", {})
        for item in (getattr(session, "participants", {}) or {}).values():
            user_id = int(item["user_id"])
            sheet = _active_sheet(session, user_id)
            if isinstance(sheet, dict) and _valid_stats(sheet.get("stats")):
                players.setdefault(str(user_id), {})["sheet"] = {
                    "stats": dict(sheet["stats"]),
                    "max_hp": int(sheet.get("max_hp", 0)),
                    "ac": int(sheet.get("ac", 0)),
                }
        campaign._save_archive(dnd_module)

    campaign._archive_campaign = archive_campaign

    original_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if not session or not dnd._is_participant_mode(session):
            return await original_parse_turn(bot, chat_id, response)
        if re.search(r"\[ACTION:END\]", str(response or ""), re.I):
            return await original_parse_turn(bot, chat_id, response)

        attack = _parse_attack(response)
        if attack:
            clean, notices = campaign._apply_metadata(session, response)
            story = campaign._record_scene(session, clean)
            body = campaign.ACTION_RE.sub("", clean).strip()
            if body:
                if notices:
                    body += "\n\n" + "\n".join(notices)
                await bot.send_message(chat_id, body)
            summary, continuation_prompt, _all_dead = _resolve_enemy_attack(session, attack)
            await bot.send_message(chat_id, summary)
            dnd.persist_dnd_sessions()
            if story:
                campaign._maybe_image(dnd, bot, session, story)
            try:
                next_response = await dnd.generate_session_response(
                    session,
                    dnd.with_scene_direction(session, continuation_prompt),
                )
                return await dnd.parse_and_execute_turn(bot, chat_id, next_response)
            except Exception:
                logging.exception("DnD enemy attack continuation failed chat_id=%s", chat_id)
                await bot.send_message(chat_id, "Мастер завис после мордобоя. Егра сохранена.")
                return await dnd.open_action_window(bot, chat_id)

        ability = _extract_roll_ability(response)
        result = await original_parse_turn(bot, chat_id, response)
        current = dnd.dnd_sessions.get(chat_id)
        if current and current.state == "WAITING_ROLL" and current.pending_roll:
            current.pending_roll["ability"] = ability or _ability_for_roll(current.pending_roll)
            dnd.persist_dnd_sessions()
        return result

    dnd.parse_and_execute_turn = parse_turn

    original_can_user_act = dnd._can_user_act

    def can_user_act(session, user_id, target_user_ids=None):
        if dnd._is_participant_mode(session):
            sheet = _active_sheet(session, int(user_id))
            if sheet is not None and not _alive(sheet):
                return False
        return original_can_user_act(session, user_id, target_user_ids)

    dnd._can_user_act = can_user_act

    original_expected = DndParticipantCompletionMiddleware._expected_ids

    def expected_ids(dnd_module, session, target_user_ids):
        expected = original_expected(dnd_module, session, target_user_ids)
        living = _living_ids(session)
        return {user_id for user_id in expected if user_id in living}

    DndParticipantCompletionMiddleware._expected_ids = staticmethod(expected_ids)

    original_from_record = dnd.GameSession.from_record.__func__

    @classmethod
    def from_record(cls, row):
        session = original_from_record(cls, row)
        raw_roll = (row or {}).get("pending_roll") or {}
        if getattr(session, "pending_roll", None) is not None:
            ability = str(raw_roll.get("ability") or "").upper()
            if ability in ABILITY_KEYS:
                session.pending_roll["ability"] = ability
        return session

    dnd.GameSession.from_record = from_record

    original_render_hero = state_commands.render_hero

    def render_hero(dnd_module, chat_id, user_id, user_name=None):
        text = original_render_hero(dnd_module, chat_id, user_id, user_name)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is not None:
            lines = _hero_combat_lines(session, user_id)
            if lines:
                return text + "\n" + "\n".join(lines)
        history = campaign._player_history(chat_id, user_id) or {}
        sheet = history.get("sheet") or {}
        if _valid_stats(sheet.get("stats")):
            history_lines = [
                f"❤️ Базовое здоровье: {sheet.get('max_hp')}",
                f"🛡 Класс брони: {sheet.get('ac')}",
                "📊 " + _stats_line(sheet["stats"]),
            ]
            return text + "\n" + "\n".join(history_lines)
        return text

    state_commands.render_hero = render_hero

    original_render_status = state_commands.render_status

    def render_status(dnd_module, chat_id):
        text = original_render_status(dnd_module, chat_id)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is None or not (getattr(session, "character_sheets", {}) or {}):
            return text
        parts = []
        for key, sheet in session.character_sheets.items():
            name = _participant_name(session, int(key))
            marker = "☠️" if not _alive(sheet) else "❤️"
            parts.append(f"{marker} {name} {sheet.get('hp', 0)}/{sheet.get('max_hp', 0)}")
        return text + "\n\n⚔️ Партия\n" + " · ".join(parts)

    state_commands.render_status = render_status

    dnd_router.message.outer_middleware(DndCombatMiddleware())
    dnd_router._upupa_dnd_combat_configured = True
