"""Deterministic player-vs-enemy combat for participant-mode DnD."""
from __future__ import annotations

import logging
import random
import re


PLAYER_COMBAT_MARKER = "АТАКИ ГЕРОЕВ ПО ПРАВИЛАМ DND УПУПЫ"
PLAYER_ATTACK_RE = re.compile(r"\[ACTION:PLAYER_ATTACK;(.*?)\]", re.I | re.S)

PLAYER_COMBAT_RULES = f"""
{PLAYER_COMBAT_MARKER}: прямую атаку героя, которая должна наносить урон NPC/монстру, НИКОГДА не разрешай обычным
ACTION:ROLL и не описывай попадание, урон, убийство или отрубание конечностей заранее. Вместо этого закончи сцену тегом:
[ACTION:PLAYER_ATTACK;TARGETS:12345;ENEMY:огр;POWER:HIGH;HP:28;AC:14;WEAPON:длинный меч;STYLE:MELEE;MODE:NORMAL;REASON:Алиса рубит огра]
TARGETS — ровно один живой герой, который атакует. ENEMY — стабильное короткое имя конкретного противника; если одинаковых
врагов несколько, различай их, например «гоблин 1» и «гоблин 2». POWER: LOW/MEDIUM/HIGH/DEADLY. HP и AC обязательны при
первом появлении противника; дальше код хранит текущее HP и КБ и не даст модели исцелить или ослабить его случайной цифрой.
WEAPON — точное оружие/предмет, которым реально атакует герой. STYLE: MELEE, RANGED или SPECIAL. MODE может быть NORMAL,
ADVANTAGE или DISADVANTAGE, только если преимущество/помеха действительно следуют из сцены.

Код сам делает бросок d20 против КБ и сам считает урон. Базовая шкала близка к D&D 5e: без оружия 1 + СИЛ; кинжал/нож,
дубина и импровизированный предмет 1d4; копьё, булава, посох, короткий меч и короткий лук 1d6; длинный меч, рапира,
боевой топор, боевой молот, длинный лук и обычный арбалет 1d8; тяжёлый арбалет 1d10; двуручный меч/молот 2d6;
двуручный топор/секира 1d12. Нераспознанный предмет считается импровизированным оружием 1d4. Сам по себе артефакт
НЕ получает огромный урон только потому, что он артефакт. Обычное оружие и безоружная атака используют бонус владения +2,
импровизированное оружие — без него. Ближний бой использует СИЛ, дальний ЛОВ, finesse берёт лучшую из СИЛ/ЛОВ.
Натуральная 1 всегда промах; натуральная 20 всегда попадание и удваивает только кубики урона, а не модификатор.

Сильный противник не может «просто умереть от красивого описания». Пока код сообщает HP > 0, противник жив и продолжает
бой. Смерть/победа допустима только когда механика реально довела HP до 0. Не сбрасывай HP противника между ходами.
""".strip()

_POWER_DEFAULTS = {
    "LOW": (8, 10),
    "MEDIUM": (16, 12),
    "HIGH": (28, 14),
    "DEADLY": (45, 16),
}

# needles, dice count, die sides, attack ability, proficiency, display label
_WEAPON_RULES = (
    (("тяжелый арбалет", "тяжёлый арбалет", "heavy crossbow"), 1, 10, "DEX", 2, "тяжёлый арбалет"),
    (("двуручный меч", "greatsword"), 2, 6, "STR", 2, "двуручный меч"),
    (("двуручный молот", "кувалд", "maul"), 2, 6, "STR", 2, "двуручный молот"),
    (("двуручный топор", "секир", "greataxe"), 1, 12, "STR", 2, "двуручный топор"),
    (("длинный лук", "longbow"), 1, 8, "DEX", 2, "длинный лук"),
    (("короткий лук", "shortbow"), 1, 6, "DEX", 2, "короткий лук"),
    (("арбалет", "crossbow"), 1, 8, "DEX", 2, "арбалет"),
    (("длинный меч", "longsword"), 1, 8, "STR", 2, "длинный меч"),
    (("короткий меч", "shortsword", "скимитар", "scimitar"), 1, 6, "FINESSE", 2, "короткий меч"),
    (("рапир", "rapier"), 1, 8, "FINESSE", 2, "рапира"),
    (("боевой топор", "battleaxe"), 1, 8, "STR", 2, "боевой топор"),
    (("боевой молот", "warhammer"), 1, 8, "STR", 2, "боевой молот"),
    (("топорик", "ручной топор", "handaxe"), 1, 6, "STR", 2, "топорик"),
    (("копь", "spear"), 1, 6, "STR", 2, "копьё"),
    (("булав", "mace"), 1, 6, "STR", 2, "булава"),
    (("посох", "quarterstaff", "staff"), 1, 6, "STR", 2, "посох"),
    (("кинжал", "dagger", "нож"), 1, 4, "FINESSE", 2, "кинжал/нож"),
    (("дубин", "club"), 1, 4, "STR", 2, "дубина"),
    (("серп", "sickle"), 1, 4, "STR", 2, "серп"),
    (("пращ", "sling"), 1, 4, "DEX", 2, "праща"),
    (("когт", "укус", "клык"), 1, 4, "STR", 2, "естественное оружие"),
    (("меч", "sword"), 1, 8, "STR", 2, "меч"),
    (("топор", "axe"), 1, 8, "STR", 2, "топор"),
    (("молот", "hammer"), 1, 8, "STR", 2, "молот"),
    (("лук", "bow"), 1, 6, "DEX", 2, "лук"),
)

_UNARMED = ("кулак", "пинок", "голыми руками", "без оружия", "unarmed", "рукопаш")


def _normalize(value: str) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", text).split())


def _safe_int(value, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _parse_fields(raw: str) -> dict[str, str]:
    fields = {}
    for part in str(raw or "").split(";"):
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return fields


def _parse_mode(value: str) -> str:
    mode = str(value or "NORMAL").upper()
    aliases = {"ADV": "ADVANTAGE", "DIS": "DISADVANTAGE", "ПРЕИМУЩЕСТВО": "ADVANTAGE", "ПОМЕХА": "DISADVANTAGE"}
    mode = aliases.get(mode, mode)
    return mode if mode in {"NORMAL", "ADVANTAGE", "DISADVANTAGE"} else "NORMAL"


def _parse_player_attack(text: str) -> dict | None:
    match = PLAYER_ATTACK_RE.search(str(text or ""))
    if not match:
        return None
    fields = _parse_fields(match.group(1))
    targets = []
    for raw in str(fields.get("TARGETS") or "").split(","):
        if raw.strip().isdigit():
            targets.append(int(raw.strip()))
    power = str(fields.get("POWER") or "MEDIUM").upper()
    if power not in _POWER_DEFAULTS:
        power = "MEDIUM"
    style = str(fields.get("STYLE") or "MELEE").upper()
    if style not in {"MELEE", "RANGED", "SPECIAL"}:
        style = "MELEE"
    return {
        "target_user_ids": targets[:1],
        "enemy_name": str(fields.get("ENEMY") or "Враг").strip()[:80] or "Враг",
        "power": power,
        "enemy_hp": fields.get("HP"),
        "enemy_ac": fields.get("AC"),
        "weapon": str(fields.get("WEAPON") or "без оружия").strip()[:120] or "без оружия",
        "style": style,
        "mode": _parse_mode(fields.get("MODE")),
        "reason": str(fields.get("REASON") or "герой атакует").strip()[:240] or "герой атакует",
    }


def _ensure_enemy_store(session) -> None:
    if not isinstance(getattr(session, "enemy_combatants", None), dict):
        session.enemy_combatants = {}


def _enemy_key(name: str) -> str:
    return _normalize(name)[:80] or "враг"


def _clean_enemy_row(key: str, row) -> dict | None:
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or key or "Враг").strip()[:80] or "Враг"
    power = str(row.get("power") or "MEDIUM").upper()
    if power not in _POWER_DEFAULTS:
        power = "MEDIUM"
    default_hp, default_ac = _POWER_DEFAULTS[power]
    max_hp = _safe_int(row.get("max_hp"), default_hp, low=1, high=999)
    hp = _safe_int(row.get("hp"), max_hp, low=0, high=max_hp)
    ac = _safe_int(row.get("ac"), default_ac, low=5, high=30)
    status = "dead" if hp <= 0 or str(row.get("status") or "alive") == "dead" else "alive"
    if status == "dead":
        hp = 0
    return {"name": name, "power": power, "hp": hp, "max_hp": max_hp, "ac": ac, "status": status}


def _restore_enemy_store(session, data) -> None:
    raw = (data or {}).get("enemy_combatants") if isinstance(data, dict) else None
    restored = {}
    if isinstance(raw, dict):
        for key, row in raw.items():
            clean = _clean_enemy_row(str(key), row)
            if clean:
                restored[_enemy_key(clean["name"])] = clean
    session.enemy_combatants = restored


def _enemy_state_field(session) -> dict:
    _ensure_enemy_store(session)
    return session.enemy_combatants


def _register_enemy(session, *, name: str, power: str, hp=None, ac=None) -> tuple[str, dict]:
    _ensure_enemy_store(session)
    key = _enemy_key(name)
    existing = session.enemy_combatants.get(key)
    if isinstance(existing, dict):
        clean = _clean_enemy_row(key, existing)
        if clean:
            session.enemy_combatants[key] = clean
            return key, clean
    power = str(power or "MEDIUM").upper()
    if power not in _POWER_DEFAULTS:
        power = "MEDIUM"
    default_hp, default_ac = _POWER_DEFAULTS[power]
    # Strong enemies get at least their calibrated tier HP. A model-provided HP can make them tougher, never paper-thin.
    max_hp = max(default_hp, _safe_int(hp, default_hp, low=1, high=999))
    armor = _safe_int(ac, default_ac, low=8, high=22)
    row = {
        "name": str(name or "Враг").strip()[:80] or "Враг",
        "power": power,
        "hp": max_hp,
        "max_hp": max_hp,
        "ac": armor,
        "status": "alive",
    }
    session.enemy_combatants[key] = row
    return key, row


def _weapon_profile(name: str, *, style: str = "MELEE") -> dict:
    normalized = _normalize(name)
    if any(_normalize(token) in normalized for token in _UNARMED):
        return {"dice_count": 0, "die_sides": 0, "base": 1, "ability": "STR", "proficiency": 2, "label": "безоружная атака", "improvised": False}
    if str(style or "").upper() == "SPECIAL":
        return {"dice_count": 1, "die_sides": 6, "base": 0, "ability": "SPECIAL", "proficiency": 2, "label": "особый приём", "improvised": False}
    for needles, count, sides, ability, proficiency, label in _WEAPON_RULES:
        if any(_normalize(needle) in normalized for needle in needles):
            return {"dice_count": count, "die_sides": sides, "base": 0, "ability": ability, "proficiency": proficiency, "label": label, "improvised": False}
    ability = "DEX" if str(style or "").upper() == "RANGED" else "STR"
    return {"dice_count": 1, "die_sides": 4, "base": 0, "ability": ability, "proficiency": 0, "label": "импровизированное оружие", "improvised": True}


def _attack_ability(profile: dict, stats: dict) -> str:
    ability = str(profile.get("ability") or "STR")
    if ability == "FINESSE":
        return "DEX" if int(stats.get("DEX", 10)) > int(stats.get("STR", 10)) else "STR"
    if ability == "SPECIAL":
        return max(("INT", "WIS", "CHA"), key=lambda key: int(stats.get(key, 10)))
    return ability if ability in {"STR", "DEX", "CON", "INT", "WIS", "CHA"} else "STR"


def _damage_notation(profile: dict) -> str:
    count = int(profile.get("dice_count") or 0)
    sides = int(profile.get("die_sides") or 0)
    if count <= 0 or sides <= 0:
        return str(int(profile.get("base") or 1))
    return f"{count}d{sides}"


def _roll_damage(profile: dict, modifier: int, *, critical: bool) -> tuple[int, list[int]]:
    count = int(profile.get("dice_count") or 0)
    sides = int(profile.get("die_sides") or 0)
    if critical and count > 0:
        count *= 2
    rolls = [random.randint(1, sides) for _ in range(count)] if count > 0 and sides > 0 else []
    base = sum(rolls) if rolls else int(profile.get("base") or 1)
    return max(1, base + int(modifier)), rolls


def _enemy_context(session) -> str:
    _ensure_enemy_store(session)
    if not session.enemy_combatants:
        return "БОЕВЫЕ ПРОТИВНИКИ: пока нет зарегистрированных."
    lines = ["БОЕВЫЕ ПРОТИВНИКИ (источник истины по HP/КБ):"]
    for enemy in session.enemy_combatants.values():
        status = "побеждён" if enemy.get("status") == "dead" else "жив"
        lines.append(
            f"- {enemy.get('name')}: POWER {enemy.get('power')}, HP {enemy.get('hp')}/{enemy.get('max_hp')}, "
            f"КБ {enemy.get('ac')}, статус={status}"
        )
    return "\n".join(lines)


def _enemy_status_lines(session) -> list[str]:
    _ensure_enemy_store(session)
    lines = []
    for enemy in session.enemy_combatants.values():
        marker = "☠️" if enemy.get("status") == "dead" else "👹"
        lines.append(f"{marker} {enemy.get('name')} — ❤️ {enemy.get('hp')}/{enemy.get('max_hp')} · 🛡 КБ {enemy.get('ac')}")
    return lines


def _sync_enemy_attack(session, attack: dict, downstream):
    name = str(attack.get("enemy_name") or "Враг")
    key, enemy = _register_enemy(
        session,
        name=name,
        power=str(attack.get("power") or "MEDIUM"),
        hp=attack.get("enemy_hp"),
        ac=attack.get("enemy_ac"),
    )
    if enemy.get("status") == "dead":
        return (
            f"☠️ {enemy['name']} уже побеждён и атаковать не может.",
            f"{enemy['name']} уже имеет 0 HP и не может атаковать. Не воскрешай его без отдельной сюжетной причины; продолжай сцену.",
            False,
        )
    synced = dict(attack)
    synced["enemy_key"] = key
    synced["enemy_name"] = enemy["name"]
    synced["enemy_hp"] = enemy["hp"]
    synced["enemy_ac"] = enemy["ac"]
    return downstream(session, synced)


def _resolve_attack_mechanics(combat, session, user_id: int, pending: dict, natural: int) -> tuple[str, str]:
    attack = pending.get("attack") or {}
    _ensure_enemy_store(session)
    enemy = session.enemy_combatants.get(str(attack.get("enemy_key") or ""))
    if not isinstance(enemy, dict):
        return "⚠️ Цель атаки потерялась.", "Цель атаки больше не существует. Продолжай сцену без выдуманного урона."
    if enemy.get("status") == "dead" or int(enemy.get("hp", 0)) <= 0:
        return f"☠️ {enemy.get('name')} уже имеет 0 HP.", f"{enemy.get('name')} уже побеждён. Продолжай сцену."

    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(int(user_id))) or {}
    stats = sheet.get("stats") if isinstance(sheet, dict) else {}
    weapon = str(attack.get("weapon") or "без оружия")
    style = str(attack.get("style") or "MELEE")
    profile = _weapon_profile(weapon, style=style)
    ability = _attack_ability(profile, stats if isinstance(stats, dict) else {})
    score = int((stats or {}).get(ability, 10)) if isinstance(stats, dict) else 10
    modifier = combat.ability_modifier(score)
    proficiency = int(profile.get("proficiency") or 0)
    total = int(natural) + modifier + proficiency
    ac = int(enemy.get("ac", 10))
    hit = int(natural) == 20 or (int(natural) != 1 and total >= ac)
    attacker = combat._participant_name(session, user_id)
    natural_note = " КРИТ." if int(natural) == 20 else (" НАТУРАЛЬНАЯ 1." if int(natural) == 1 else "")
    attack_line = (
        f"🎯 Атака: {natural} {modifier:+d} {proficiency:+d} = {total} против КБ {ac} — "
        + ("попадание." if hit else "промах.")
        + natural_note
    )
    if not hit:
        summary = (
            f"⚔️ {attacker} → {enemy['name']} ({weapon}; {_damage_notation(profile)}).\n"
            f"{attack_line}\n👹 {enemy['name']} — ❤️ {enemy['hp']}/{enemy['max_hp']} · 🛡 КБ {enemy['ac']}"
        )
        prompt = (
            f"{attacker} атаковал {enemy['name']} оружием «{weapon}»: бросок {natural} + модификатор {modifier:+d} + "
            f"владение {proficiency:+d} = {total} против КБ {ac}; промах. Урон 0. "
            f"У {enemy['name']} остаётся {enemy['hp']}/{enemy['max_hp']} HP. Продолжай бой с этим точным исходом."
        )
        return summary, prompt

    damage, rolls = _roll_damage(profile, modifier, critical=int(natural) == 20)
    old_hp = int(enemy.get("hp", 0))
    enemy["hp"] = max(0, old_hp - damage)
    if enemy["hp"] <= 0:
        enemy["hp"] = 0
        enemy["status"] = "dead"
    dice_text = "+".join(str(value) for value in rolls) if rolls else str(int(profile.get("base") or 1))
    rolled_notation = _damage_notation(profile)
    if int(natural) == 20 and int(profile.get("dice_count") or 0) > 0:
        rolled_notation = f"{int(profile['dice_count']) * 2}d{int(profile['die_sides'])}"
    damage_line = f"💥 Урон: {rolled_notation} ({dice_text}) {modifier:+d} = {damage}."
    summary = (
        f"⚔️ {attacker} → {enemy['name']} ({weapon}).\n{attack_line}\n{damage_line}\n"
        f"👹 {enemy['name']} — ❤️ {enemy['hp']}/{enemy['max_hp']} · 🛡 КБ {enemy['ac']}"
    )
    if enemy["status"] == "dead":
        summary += f"\n☠️ {enemy['name']} падает до 0 HP."
        prompt = (
            f"{attacker} попал по {enemy['name']} оружием «{weapon}». Урон {damage}; HP было {old_hp}, стало 0. "
            f"{enemy['name']} побеждён. Можешь описать поражение противника и продолжить сцену."
        )
    else:
        prompt = (
            f"{attacker} попал по {enemy['name']} оружием «{weapon}». Урон {damage}; HP было {old_hp}, стало {enemy['hp']} "
            f"из {enemy['max_hp']}. {enemy['name']} ЖИВ. Не описывай его смерть, окончательное обезвреживание или исчезновение: "
            "продолжай бой с этим точным остатком HP."
        )
    if int(natural) == 20:
        prompt += " Это натуральная 20; удвоены только кубики урона."
    return summary, prompt


async def _begin_player_attack(dnd, combat, campaign, bot, chat_id: int, session, response: str, attack: dict) -> None:
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

    targets = dnd._resolve_targets(session, attack.get("target_user_ids") or [])
    living = combat._living_ids(session)
    targets = [user_id for user_id in targets if user_id in living]
    if not targets:
        targets = sorted(living)[:1]
    if not targets:
        await bot.send_message(chat_id, "☠️ Атаковать некому: живых героев не осталось.")
        return await dnd.open_action_window(bot, chat_id)
    user_id = int(targets[0])

    enemy_key, enemy = _register_enemy(
        session,
        name=attack["enemy_name"],
        power=attack["power"],
        hp=attack.get("enemy_hp"),
        ac=attack.get("enemy_ac"),
    )
    if enemy.get("status") == "dead":
        await bot.send_message(chat_id, f"☠️ {enemy['name']} уже имеет 0 HP. Бить труп кубиком не обязательно.")
        return await dnd.open_action_window(bot, chat_id)

    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(user_id)) or {}
    stats = sheet.get("stats") if isinstance(sheet, dict) else {}
    profile = _weapon_profile(attack["weapon"], style=attack["style"])
    ability = _attack_ability(profile, stats if isinstance(stats, dict) else {})
    modifier = combat.ability_modifier(int((stats or {}).get(ability, 10))) if isinstance(stats, dict) else 0
    session.pending_roll = {
        "type": "ATTACK",
        "skill": None,
        "reason": attack["reason"],
        "dc": enemy["ac"],
        "mode": attack["mode"],
        "target_user_ids": [user_id],
        "ability": ability,
        "attack": {
            "enemy_key": enemy_key,
            "enemy_name": enemy["name"],
            "weapon": attack["weapon"],
            "style": attack["style"],
        },
    }
    session.state = "WAITING_ROLL"
    session.action_prompt_message_id = None
    session.pending_actions = {}
    session.action_deadline = None
    session.action_target_user_ids = []
    dnd.persist_dnd_sessions()
    mode_text = ""
    if attack["mode"] == "ADVANTAGE":
        mode_text = " · преимущество"
    elif attack["mode"] == "DISADVANTAGE":
        mode_text = " · помеха"
    await bot.send_message(
        chat_id,
        f"👹 {enemy['name']} — ❤️ {enemy['hp']}/{enemy['max_hp']} HP · 🛡 КБ {enemy['ac']}\n"
        f"⚔️ {combat._participant_name(session, user_id)} атакует {enemy['name']}: «{attack['weapon']}» — "
        f"{_damage_notation(profile)} + {ability} {modifier:+d}; КБ цели {enemy['ac']}{mode_text}. Пиши «кидаю».",
    )
    if story:
        campaign._maybe_image(dnd, bot, session, story)


async def _resolve_player_attack_roll(dnd, combat, message, session) -> None:
    pending = getattr(session, "pending_roll", None) or {}
    user_id = int(message.from_user.id)
    if not dnd._can_user_act(session, user_id, pending.get("target_user_ids") or []):
        await message.answer("Этот бросок не твой. Или ты уже героически помер.")
        return
    rolls, natural = dnd._roll_d20(pending.get("mode", "NORMAL"))
    summary, prompt = _resolve_attack_mechanics(combat, session, user_id, pending, natural)
    if len(rolls) > 1:
        summary = f"🎲 Кубики: {rolls[0]} и {rolls[1]}, выбран {natural}.\n" + summary
    session.state = "RESOLVING"
    session.pending_roll = None
    dnd.persist_dnd_sessions()
    await message.answer(summary)
    try:
        response_text = await dnd.generate_session_response(
            session,
            dnd.with_scene_direction(session, prompt),
        )
        await dnd.parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD player attack continuation failed chat_id=%s", message.chat.id)
        await message.answer("Мастер завис после удара, но HP и результат атаки сохранены.")
        await dnd.open_action_window(message.bot, message.chat.id)


def install_dnd_player_combat(dnd, *, state_policy) -> None:
    """Install persistent enemies and deterministic hero attack/damage resolution."""
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    from AI import dnd_state_commands as state_commands

    if getattr(dnd, "_upupa_dnd_player_combat_installed", False):
        return

    state_policy.add_ensure_hook(_ensure_enemy_store)
    state_policy.add_state_field("enemy_combatants", _enemy_state_field)
    state_policy.add_restore_hook(_restore_enemy_store)

    original_combat_context = combat._combat_context

    def combat_context(session):
        return original_combat_context(session) + "\n" + _enemy_context(session)

    combat._combat_context = combat_context

    original_enemy_attack = combat._resolve_enemy_attack

    def resolve_enemy_attack(session, attack):
        return _sync_enemy_attack(session, attack, original_enemy_attack)

    combat._resolve_enemy_attack = resolve_enemy_attack

    original_resolve_player_roll = combat._resolve_player_roll

    async def resolve_player_roll(dnd_module, message, session):
        pending = getattr(session, "pending_roll", None) or {}
        if str(pending.get("type") or "").upper() == "ATTACK":
            return await _resolve_player_attack_roll(dnd_module, combat, message, session)
        return await original_resolve_player_roll(dnd_module, message, session)

    combat._resolve_player_roll = resolve_player_roll

    original_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if not session or not dnd._is_participant_mode(session):
            return await original_parse_turn(bot, chat_id, response)
        attack = _parse_player_attack(response)
        if attack:
            return await _begin_player_attack(dnd, combat, campaign, bot, chat_id, session, response, attack)
        return await original_parse_turn(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn

    original_render_status = state_commands.render_status

    def render_status(dnd_module, chat_id):
        text = original_render_status(dnd_module, chat_id)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is None:
            return text
        lines = _enemy_status_lines(session)
        if not lines:
            return text
        return text + "\n👹 Противники:\n" + "\n".join(lines)

    state_commands.render_status = render_status

    if PLAYER_COMBAT_MARKER not in combat.COMBAT_RULES:
        combat.COMBAT_RULES = combat.COMBAT_RULES.rstrip() + "\n" + PLAYER_COMBAT_RULES
    if PLAYER_COMBAT_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + PLAYER_COMBAT_RULES

    dnd._upupa_dnd_player_combat_installed = True
