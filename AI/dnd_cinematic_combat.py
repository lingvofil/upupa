"""Rare environmental combat actions that can bypass ordinary enemy HP."""
from __future__ import annotations

import logging
import re


CINEMATIC_COMBAT_MARKER = "КИНЕМАТОГРАФИЧЕСКИЕ СМЕРТЕЛЬНЫЕ МАНЁВРЫ"
CINEMATIC_ATTACK_RE = re.compile(r"\[ACTION:CINEMATIC_ATTACK;(.*?)\]", re.I | re.S)

CINEMATIC_COMBAT_RULES = f"""
{CINEMATIC_COMBAT_MARKER}: это ЕДИНСТВЕННОЕ исключение из правила, что живого сильного противника нельзя победить
красивым описанием в обход HP. Используй его редко и только для нестандартного действия с окружением или заранее
созданной ситуацией, где успешное действие физически способно сразу убить или окончательно обезвредить цель независимо
от обычного оружейного урона: например, обрушить на врага действительно огромный валун с высоты, сбросить его в лаву,
обрушить несущую конструкцию или запустить уже подготовленный смертельный механизм.

НЕЛЬЗЯ использовать эту механику для обычного удара оружием, выстрела, особого приёма, заклинания обычного урона,
"удара посильнее", "попытки отрубить голову" или просто потому, что игрок эффектно описал атаку. Желание игрока убить
с одного раза само по себе не создаёт право на ваншот. Нужная опасность должна реально следовать из уже установленной
сцены; не дорисовывай удобный валун, люстру, лаву, пропасть или ловушку задним числом только ради такого исхода.
Если действие по сути остаётся обычной атакой, используй ACTION:PLAYER_ATTACK и стандартные HP/КБ.

Для допустимого смертельного манёвра закончи сцену тегом:
[ACTION:CINEMATIC_ATTACK;TARGETS:12345;ENEMY:огр;POWER:HIGH;HP:28;AC:14;ABILITY:STR;DC:16;MODE:NORMAL;OBJECT:валун-на-скале;METHOD:свалить огромный валун со скалы на огра;REASON:Алиса толкает валун в момент, когда огр проходит внизу]
TARGETS — ровно один живой герой, совершающий действие. ENEMY — стабильное имя конкретной цели. POWER, HP и AC задавай
так же, как для ACTION:PLAYER_ATTACK; существующее HP врага код не даст сбросить. ABILITY — STR/DEX/CON/INT/WIS/CHA,
которая действительно отвечает за ключевую часть манёвра. DC — сложность 5..20; код всё равно не позволит смертельной
проверке быть легче 12/14/16/18 для LOW/MEDIUM/HIGH/DEADLY. MODE: NORMAL/ADVANTAGE/DISADVANTAGE только по ситуации.
METHOD — кратко сам физический трюк, REASON — что именно делает герой.

Код сам бросает d20 и добавляет модификатор выбранной характеристики. Натуральная 1 всегда провал, натуральная 20 всегда
успех. При успехе код переводит цель в 0 HP независимо от прежнего остатка: после этого можно описать её смерть или
окончательное физическое обезвреживание. При провале HP цели НЕ уменьшается вообще: не превращай провал в бесплатную
обычную атаку, а дай конкретное последствие — потерянную позицию/возможность, ответный риск, шум, разрушение укрытия и т.п.
""".strip()

_MIN_DC_BY_POWER = {
    "LOW": 12,
    "MEDIUM": 14,
    "HIGH": 16,
    "DEADLY": 18,
}
_VALID_ABILITIES = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
_VALID_MODES = {"NORMAL", "ADVANTAGE", "DISADVANTAGE"}


def _parse_fields(raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return fields


def _safe_int(value, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _parse_mode(value: str) -> str:
    mode = str(value or "NORMAL").upper()
    aliases = {
        "ADV": "ADVANTAGE",
        "DIS": "DISADVANTAGE",
        "ПРЕИМУЩЕСТВО": "ADVANTAGE",
        "ПОМЕХА": "DISADVANTAGE",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in _VALID_MODES else "NORMAL"


def _parse_cinematic_attack(text: str) -> dict | None:
    match = CINEMATIC_ATTACK_RE.search(str(text or ""))
    if not match:
        return None
    fields = _parse_fields(match.group(1))
    targets = []
    for raw in str(fields.get("TARGETS") or "").split(","):
        if raw.strip().isdigit():
            targets.append(int(raw.strip()))

    power = str(fields.get("POWER") or "MEDIUM").upper()
    if power not in _MIN_DC_BY_POWER:
        power = "MEDIUM"
    ability = str(fields.get("ABILITY") or "STR").upper()
    if ability not in _VALID_ABILITIES:
        ability = "STR"
    requested_dc = _safe_int(fields.get("DC"), _MIN_DC_BY_POWER[power], low=5, high=20)

    return {
        "target_user_ids": targets[:1],
        "enemy_name": str(fields.get("ENEMY") or "Враг").strip()[:80] or "Враг",
        "power": power,
        "enemy_hp": fields.get("HP"),
        "enemy_ac": fields.get("AC"),
        "ability": ability,
        "requested_dc": requested_dc,
        "mode": _parse_mode(fields.get("MODE")),
        "method": str(fields.get("METHOD") or "опасный трюк с окружением").strip()[:240]
        or "опасный трюк с окружением",
        "reason": str(fields.get("REASON") or "герой пытается провести смертельный манёвр").strip()[:300]
        or "герой пытается провести смертельный манёвр",
    }


def _cinematic_dc(action: dict, enemy: dict) -> int:
    power = str(enemy.get("power") or action.get("power") or "MEDIUM").upper()
    floor = _MIN_DC_BY_POWER.get(power, _MIN_DC_BY_POWER["MEDIUM"])
    requested = _safe_int(action.get("requested_dc"), floor, low=5, high=20)
    return max(floor, requested)


def _ability_modifier(combat, session, user_id: int, ability: str) -> int:
    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(int(user_id))) or {}
    stats = sheet.get("stats") if isinstance(sheet, dict) else {}
    score = int((stats or {}).get(ability, 10)) if isinstance(stats, dict) else 10
    return combat.ability_modifier(score)


def _resolve_cinematic_mechanics(combat, player_combat, session, user_id: int, pending: dict, natural: int):
    action = pending.get("cinematic") or {}
    player_combat._ensure_enemy_store(session)
    enemy = session.enemy_combatants.get(str(action.get("enemy_key") or ""))
    if not isinstance(enemy, dict):
        return "⚠️ Цель манёвра потерялась.", "Цель смертельного манёвра больше не существует. Продолжай сцену без выдуманного урона."
    if enemy.get("status") == "dead" or int(enemy.get("hp", 0)) <= 0:
        return f"☠️ {enemy.get('name')} уже имеет 0 HP.", f"{enemy.get('name')} уже побеждён. Продолжай сцену."

    ability = str(action.get("ability") or "STR").upper()
    modifier = _ability_modifier(combat, session, user_id, ability)
    dc = int(pending.get("dc") or _cinematic_dc(action, enemy))
    total = int(natural) + modifier
    success = int(natural) == 20 or (int(natural) != 1 and total >= dc)
    actor = combat._participant_name(session, user_id)
    method = str(action.get("method") or "смертельный трюк с окружением")
    old_hp = int(enemy.get("hp", 0))

    natural_note = " · КРИТИЧЕСКАЯ УДАЧА" if int(natural) == 20 else (
        " · КРИТИЧЕСКАЯ НЕУДАЧА" if int(natural) == 1 else ""
    )
    check_line = f"🎯 Проверка: {natural} {modifier:+d} = {total} против сложности {dc}{natural_note}."

    if success:
        enemy["hp"] = 0
        enemy["status"] = "dead"
        summary = (
            f"🎬 {actor} → {enemy['name']}: {method}.\n"
            f"{check_line}\n"
            f"💥 Смертельный манёвр успешен: ❤️ {old_hp}/{enemy['max_hp']} → 0/{enemy['max_hp']}.\n"
            f"☠️ {enemy['name']} побеждён нестандартным действием."
        )
        prompt = (
            f"{actor} успешно провёл допустимый смертельный манёвр против {enemy['name']}: «{method}». "
            f"Проверка {ability}: d20={natural}, модификатор {modifier:+d}, итог {total} против сложности {dc}. "
            f"Это специальное механическое исключение: HP цели было {old_hp}/{enemy['max_hp']}, код установил 0 HP. "
            "Опиши физически правдоподобный результат — смерть или окончательное обезвреживание цели — и не отменяй этот исход."
        )
        return summary, prompt

    summary = (
        f"🎬 {actor} → {enemy['name']}: {method}.\n"
        f"{check_line}\n"
        f"❌ Манёвр провален. HP цели не меняется: ❤️ {enemy['hp']}/{enemy['max_hp']}."
    )
    prompt = (
        f"{actor} провалил смертельный манёвр против {enemy['name']}: «{method}». "
        f"Проверка {ability}: d20={natural}, модификатор {modifier:+d}, итог {total} против сложности {dc}. "
        f"HP цели остаётся ровно {enemy['hp']}/{enemy['max_hp']}; не наноси ей бесплатный обычный урон и не описывай её смерть. "
        "Дай конкретное и логичное последствие провала и продолжай сцену."
    )
    return summary, prompt


async def _begin_cinematic_attack(dnd, combat, campaign, player_combat, bot, chat_id: int, session, response: str, action: dict):
    clean, notices = campaign._apply_metadata(session, response)
    story = campaign._record_scene(session, clean)
    body = campaign.ACTION_RE.sub("", clean).strip()
    chunks = []
    if body:
        chunks.append(body)
    if notices:
        chunks.append("\n".join(notices))
    if chunks:
        await bot.send_message(chat_id, "\n\n".join(chunks))

    targets = dnd._resolve_targets(session, action.get("target_user_ids") or [])
    living = combat._living_ids(session)
    targets = [user_id for user_id in targets if user_id in living]
    if not targets:
        targets = sorted(living)[:1]
    if not targets:
        await bot.send_message(chat_id, "☠️ Смертельный трюк исполнять некому: живых героев не осталось.")
        return await dnd.open_action_window(bot, chat_id)
    user_id = int(targets[0])

    enemy_key, enemy = player_combat._register_enemy(
        session,
        name=action["enemy_name"],
        power=action["power"],
        hp=action.get("enemy_hp"),
        ac=action.get("enemy_ac"),
    )
    if enemy.get("status") == "dead":
        await bot.send_message(chat_id, f"☠️ {enemy['name']} уже имеет 0 HP. Второй валун можно оставить на потом.")
        return await dnd.open_action_window(bot, chat_id)

    dc = _cinematic_dc(action, enemy)
    modifier = _ability_modifier(combat, session, user_id, action["ability"])
    session.pending_roll = {
        "type": "CINEMATIC_ATTACK",
        "skill": None,
        "reason": action["reason"],
        "dc": dc,
        "mode": action["mode"],
        "target_user_ids": [user_id],
        "cinematic": {
            "enemy_key": enemy_key,
            "enemy_name": enemy["name"],
            "ability": action["ability"],
            "method": action["method"],
            "power": enemy["power"],
            "requested_dc": action["requested_dc"],
        },
    }
    session.state = "WAITING_ROLL"
    session.action_prompt_message_id = None
    session.pending_actions = {}
    session.action_deadline = None
    session.action_target_user_ids = []
    dnd.persist_dnd_sessions()

    mode_text = ""
    if action["mode"] == "ADVANTAGE":
        mode_text = " · преимущество"
    elif action["mode"] == "DISADVANTAGE":
        mode_text = " · помеха"
    await bot.send_message(
        chat_id,
        f"🎬 {combat._participant_name(session, user_id)} затевает смертельный манёвр против {enemy['name']}: "
        f"«{action['method']}». Проверка {action['ability']} {modifier:+d}, сложность {dc}{mode_text}. "
        "Успех может сразу победить цель; провал не снимет ей HP. Пиши «кидаю».",
    )
    if story:
        campaign._maybe_image(dnd, bot, session, story)


async def _resolve_cinematic_roll(dnd, combat, player_combat, message, session):
    pending = getattr(session, "pending_roll", None) or {}
    user_id = int(message.from_user.id)
    if not dnd._can_user_act(session, user_id, pending.get("target_user_ids") or []):
        await message.answer("Этот бросок не твой. Или ты уже героически помер.")
        return

    rolls, natural = dnd._roll_d20(pending.get("mode", "NORMAL"))
    summary, prompt = _resolve_cinematic_mechanics(combat, player_combat, session, user_id, pending, natural)
    if len(rolls) > 1:
        summary = f"🎲 Кубики: {rolls[0]} и {rolls[1]}, выбран {natural}.\n" + summary
    session.state = "RESOLVING"
    session.pending_roll = None
    dnd.persist_dnd_sessions()
    await message.answer(summary)

    try:
        response_text = await dnd.generate_session_response(session, dnd.with_scene_direction(session, prompt))
        await dnd.parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD cinematic combat continuation failed chat_id=%s", message.chat.id)
        await message.answer("Мастер завис после трюка, но механический результат сохранён.")
        await dnd.open_action_window(message.bot, message.chat.id)


def install_dnd_cinematic_combat(dnd) -> None:
    """Install rare lethal environmental actions on top of persistent player combat."""
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    from AI import dnd_player_combat as player_combat

    if getattr(dnd, "_upupa_dnd_cinematic_combat_installed", False):
        return

    original_resolve_player_roll = combat._resolve_player_roll

    async def resolve_player_roll(dnd_module, message, session):
        pending = getattr(session, "pending_roll", None) or {}
        if str(pending.get("type") or "").upper() == "CINEMATIC_ATTACK":
            return await _resolve_cinematic_roll(dnd_module, combat, player_combat, message, session)
        return await original_resolve_player_roll(dnd_module, message, session)

    combat._resolve_player_roll = resolve_player_roll

    original_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if not session or not dnd._is_participant_mode(session):
            return await original_parse_turn(bot, chat_id, response)
        action = _parse_cinematic_attack(response)
        if action:
            return await _begin_cinematic_attack(
                dnd,
                combat,
                campaign,
                player_combat,
                bot,
                chat_id,
                session,
                response,
                action,
            )
        return await original_parse_turn(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn

    if CINEMATIC_COMBAT_MARKER not in combat.COMBAT_RULES:
        combat.COMBAT_RULES = combat.COMBAT_RULES.rstrip() + "\n" + CINEMATIC_COMBAT_RULES
    if CINEMATIC_COMBAT_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + CINEMATIC_COMBAT_RULES

    dnd._upupa_dnd_cinematic_combat_installed = True
