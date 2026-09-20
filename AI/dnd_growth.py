"""Earned DnD achievements grown from repeated resolved player behavior."""
from __future__ import annotations

import copy
import re
import uuid

from aiogram import BaseMiddleware, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from AI import dnd_inventory_fun as inventory_fun


GROWTH_MARKER = "РОСТ ГЕРОЯ ИЗ ПОСТУПКОВ DND УПУПЫ"
GROWTH_THRESHOLD = 3
MAX_ACHIEVEMENTS = 3

PATTERNS = {
    "NEGOTIATE_MONSTER": "договариваться с чудовищами и другими явно опасными нечеловеческими существами",
    "IMPROVISED_WEAPON": "решать драки мебелью, посудой и другим импровизированным оружием",
    "ESCAPE": "выбираться из опасности побегом, погоней или рискованной эвакуацией",
    "DECEIVE": "решать проблемы ложью, легендами, подделками и блефом",
    "INVESTIGATE": "добывать важные сведения поиском, наблюдением и расследованием",
    "USE_ENVIRONMENT": "решать проблемы через окружение, механизмы, архитектуру и физику сцены",
}

ACHIEVEMENT_OPTIONS = {
    "NEGOTIATE_MONSTER": (
        {
            "title": "Адвокат нечисти",
            "mechanic": "PARLEY",
            "description": "раз за приключение потребовать короткую возможность переговоров до первого вражеского удара",
        },
        {
            "title": "Дипломат с клыками",
            "mechanic": "ADVANTAGE_SOCIAL",
            "description": "раз за приключение получить преимущество на подходящую социальную проверку",
        },
    ),
    "IMPROVISED_WEAPON": (
        {
            "title": "Мебельный спецназ",
            "mechanic": "ADVANTAGE_COMBAT",
            "description": "раз за приключение получить преимущество на подходящую боевую проверку или атаку",
        },
        {
            "title": "Завхоз апокалипсиса",
            "mechanic": "CLOCK_PUSH",
            "description": "раз за приключение сдвинуть подходящую шкалу прогресса на +2 через подручное решение",
        },
    ),
    "ESCAPE": (
        {
            "title": "Мастер заднего хода",
            "mechanic": "ADVANTAGE_MOVE",
            "description": "раз за приключение получить преимущество на побег, погоню или рискованное перемещение",
        },
        {
            "title": "Исчезающий свидетель",
            "mechanic": "DANGER_REDUCE",
            "description": "раз за приключение снизить активную шкалу опасности на 1",
        },
    ),
    "DECEIVE": (
        {
            "title": "Лицо кирпичом",
            "mechanic": "ADVANTAGE_SOCIAL",
            "description": "раз за приключение получить преимущество на подходящий блеф или социальную проверку",
        },
        {
            "title": "Бюрократ тумана",
            "mechanic": "DANGER_REDUCE",
            "description": "раз за приключение снизить активную шкалу опасности на 1",
        },
    ),
    "INVESTIGATE": (
        {
            "title": "Нос в каждую дыру",
            "mechanic": "ADVANTAGE_PERCEPTION",
            "description": "раз за приключение получить преимущество на поиск, наблюдение или расследование",
        },
        {
            "title": "Следователь на минималках",
            "mechanic": "CLOCK_PUSH",
            "description": "раз за приключение сдвинуть подходящую шкалу прогресса на +2 найденной зацепкой",
        },
    ),
    "USE_ENVIRONMENT": (
        {
            "title": "Архитектор несчастного случая",
            "mechanic": "ADVANTAGE_COMBAT",
            "description": "раз за приключение получить преимущество на боевой трюк с окружением",
        },
        {
            "title": "Рычаг цивилизации",
            "mechanic": "CLOCK_PUSH",
            "description": "раз за приключение сдвинуть подходящую шкалу прогресса на +2 за счёт окружения",
        },
    ),
}

VALID_MECHANICS = {
    "PARLEY",
    "ADVANTAGE_MOVE",
    "ADVANTAGE_SOCIAL",
    "ADVANTAGE_PERCEPTION",
    "ADVANTAGE_COMBAT",
    "DANGER_REDUCE",
    "CLOCK_PUSH",
}

GROWTH_RULES = f"""
{GROWTH_MARKER}.
Герой может получить постоянное достижение только из повторяющихся РЕАЛЬНО РАЗРЕШЁННЫХ поступков.
Для каждого игрока в одном разрешённом ответе отмечай максимум ОДИН доминирующий паттерн и только если действие действительно
состоялось в мире. Не отмечай голую заявку до требуемого броска; после броска можно отметить поведение независимо от успеха,
если герой реально его совершил. Не отмечай пассивно случившееся с героем событие.
Допустимые PATTERN:
NEGOTIATE_MONSTER — {PATTERNS["NEGOTIATE_MONSTER"]};
IMPROVISED_WEAPON — {PATTERNS["IMPROVISED_WEAPON"]};
ESCAPE — {PATTERNS["ESCAPE"]};
DECEIVE — {PATTERNS["DECEIVE"]};
INVESTIGATE — {PATTERNS["INVESTIGATE"]};
USE_ENVIRONMENT — {PATTERNS["USE_ENVIRONMENT"]}.
Формат:
[GROWTH:ADD;PLAYER:123;PATTERN:NEGOTIATE_MONSTER;EVIDENCE:уговорил огра пропустить отряд]
EVIDENCE — короткий конкретный факт именно этой сцены. Код считает повторения и сам предлагает развитие после партии.
Не выдавай достижения напрямую и не придумывай числовые бонусы.

Выбранные достижения — постоянная часть живого героя, но у каждого один заряд на приключение.
Игрок применяет их обычным действием фразой «использую достижение <название>».
Код сам проверяет заряд и механику; не расходуй и не усиливай достижение повествовательно сверх его описания.
""".strip()

_GROWTH_RE = re.compile(r"\[GROWTH:([^\]]*)\]", re.I)
_ACTION_RE = re.compile(r"\[ACTION:([A-Z_]+)([^\]]*)\]", re.I | re.S)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)
_MODE_RE = re.compile(r"(?:^|;)MODE:([A-Z_]+)(?=;|$)", re.I)
_DOMAIN_RE = re.compile(r"(?:^|;)DOMAIN:([A-Z_]+)(?=;|$)", re.I)
_ACH_FACT_RE = re.compile(r"\[ACHIEVEMENTFACT:([^\]]*)\]", re.I)
_USE_PREFIXES = ("использую достижение ", "применяю достижение ")
_ADVANTAGE_DOMAIN = {
    "ADVANTAGE_MOVE": "MOVE",
    "ADVANTAGE_SOCIAL": "SOCIAL",
    "ADVANTAGE_PERCEPTION": "PERCEPTION",
    "ADVANTAGE_COMBAT": "COMBAT",
}


def _clean(value, limit=220):
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _parse_fields(raw):
    parts = [part.strip() for part in str(raw or "").split(";") if part.strip()]
    head = parts[0].upper() if parts else ""
    fields = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return head, fields


def _participant_ids(session):
    result = set()
    for key, row in (getattr(session, "participants", {}) or {}).items():
        try:
            result.add(str(int(row.get("user_id", key))))
        except (TypeError, ValueError):
            continue
    return result


def _ensure(session):
    if not isinstance(getattr(session, "growth_counts", None), dict):
        session.growth_counts = {}
    if not isinstance(getattr(session, "growth_evidence", None), dict):
        session.growth_evidence = {}
    if not isinstance(getattr(session, "growth_expected_actor_ids", None), list):
        session.growth_expected_actor_ids = []
    if not isinstance(getattr(session, "growth_seen_scene_keys", None), list):
        session.growth_seen_scene_keys = []
    if not isinstance(getattr(session, "learned_achievements", None), dict):
        session.learned_achievements = {}
    if not isinstance(getattr(session, "pending_achievement_uses", None), dict):
        session.pending_achievement_uses = {}
    if not isinstance(getattr(session, "achievement_boosts", None), dict):
        session.achievement_boosts = {}
    if not isinstance(getattr(session, "achievement_world_facts", None), list):
        session.achievement_world_facts = []
    if not isinstance(getattr(session, "growth_created_offer_tokens", None), list):
        session.growth_created_offer_tokens = []


def _restore(session, data):
    row = data if isinstance(data, dict) else {}
    session.growth_counts = copy.deepcopy(row.get("growth_counts") or {})
    session.growth_evidence = copy.deepcopy(row.get("growth_evidence") or {})
    session.growth_expected_actor_ids = list(row.get("growth_expected_actor_ids") or [])
    session.growth_seen_scene_keys = list(row.get("growth_seen_scene_keys") or [])
    session.learned_achievements = copy.deepcopy(row.get("learned_achievements") or {})
    session.pending_achievement_uses = copy.deepcopy(row.get("pending_achievement_uses") or {})
    session.achievement_boosts = copy.deepcopy(row.get("achievement_boosts") or {})
    session.achievement_world_facts = copy.deepcopy(row.get("achievement_world_facts") or [])
    session.growth_created_offer_tokens = []
    _ensure(session)


def _achievement_id(pattern, mechanic):
    return f"{str(pattern).lower()}:{str(mechanic).lower()}"


def _normalise_achievement(item):
    if not isinstance(item, dict):
        return None
    pattern = str(item.get("pattern") or "").upper()
    mechanic = str(item.get("mechanic") or "").upper()
    title = _clean(item.get("title"), 90)
    description = _clean(item.get("description"), 220)
    if pattern not in PATTERNS or mechanic not in VALID_MECHANICS or not title:
        return None
    return {
        "id": str(item.get("id") or _achievement_id(pattern, mechanic)),
        "title": title,
        "description": description,
        "pattern": pattern,
        "mechanic": mechanic,
        "charges_max": 1,
        "charges_remaining": 1 if int(item.get("charges_remaining", 1) or 0) > 0 else 0,
    }


def _load_achievements_from_history(session, user_id, history):
    _ensure(session)
    key = str(int(user_id))
    if not isinstance(history, dict) or history.get("dead"):
        session.learned_achievements[key] = []
        return
    result = []
    for raw in history.get("achievements") or []:
        item = _normalise_achievement(raw)
        if item is None:
            continue
        item["charges_remaining"] = 1
        result.append(item)
    session.learned_achievements[key] = result[:MAX_ACHIEVEMENTS]


def _growth_context(session):
    _ensure(session)
    blocks = []
    achievement_lines = []
    for player, items in session.learned_achievements.items():
        participant = (getattr(session, "participants", {}) or {}).get(str(player), {})
        who = participant.get("name") or f"ID {player}"
        for item in items or []:
            if isinstance(item, dict):
                achievement_lines.append(
                    f"- {who}: {item['title']} — {item['description']} (заряд {item.get('charges_remaining', 0)}/1)"
                )
    if achievement_lines:
        blocks.append("ДОСТИЖЕНИЯ ГЕРОЕВ:\n" + "\n".join(achievement_lines))
    if session.achievement_world_facts:
        lines = [f"- {fact.get('text')}" for fact in session.achievement_world_facts[-4:] if fact.get("text")]
        if lines:
            blocks.append("АКТИВНЫЕ ФАКТЫ ДОСТИЖЕНИЙ:\n" + "\n".join(lines))
    return "\n".join(blocks)


def _record_growth(session, fields):
    _ensure(session)
    player = str(fields.get("PLAYER") or "")
    pattern = str(fields.get("PATTERN") or "").upper()
    evidence = _clean(fields.get("EVIDENCE"), 180)
    expected = {str(int(value)) for value in session.growth_expected_actor_ids if str(value).lstrip("-").isdigit()}
    if player not in _participant_ids(session) or player not in expected or pattern not in PATTERNS or not evidence:
        return None

    scene = int(getattr(session, "scene_count", 0) or 0)
    scene_key = f"{scene}:{player}"
    if scene_key in session.growth_seen_scene_keys:
        return None
    session.growth_seen_scene_keys.append(scene_key)
    session.growth_seen_scene_keys = session.growth_seen_scene_keys[-40:]

    counts = session.growth_counts.setdefault(player, {})
    counts[pattern] = int(counts.get(pattern, 0) or 0) + 1
    evidence_map = session.growth_evidence.setdefault(player, {})
    rows = list(evidence_map.get(pattern) or [])
    rows.append(evidence)
    evidence_map[pattern] = rows[-6:]
    return f"🌱 Поведение закрепляется: {evidence}."


def apply_growth_metadata(session, original_text, cleaned, notices):
    extra = []
    action, unresolved_targets = _targets_from_action(original_text)
    unresolved = set(unresolved_targets) if action in {"ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"} else set()
    for match in _GROWTH_RE.finditer(str(original_text or "")):
        head, fields = _parse_fields(match.group(1))
        if head != "ADD":
            continue
        player = str(fields.get("PLAYER") or "")
        if player.isdigit() and int(player) in unresolved:
            continue
        notice = _record_growth(session, fields)
        if notice:
            extra.append(notice)
    return _GROWTH_RE.sub("", str(cleaned or "")).strip(), list(notices or []) + extra


def _targets_from_action(response):
    match = _ACTION_RE.search(str(response or ""))
    if not match:
        return None, []
    action = match.group(1).upper()
    suffix = match.group(2) or ""
    target_match = _TARGETS_RE.search(";" + suffix.strip(";") + ";")
    targets = []
    if target_match:
        targets = [int(value) for value in target_match.group(1).split(",") if value.strip().isdigit()]
    return action, targets


def _group_actor_ids(session):
    result = []
    for row in (getattr(session, "pending_actions", {}) or {}).values():
        try:
            user_id = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue
        if user_id not in result:
            result.append(user_id)
    return result


def _eligible_patterns(history, session, user_id):
    key = str(int(user_id))
    old_progress = dict((history or {}).get("growth_progress") or {})
    current = dict((getattr(session, "growth_counts", {}) or {}).get(key) or {})
    mastered = set((history or {}).get("mastered_growth_patterns") or [])
    totals = {}
    for pattern in PATTERNS:
        totals[pattern] = int(old_progress.get(pattern, 0) or 0) + int(current.get(pattern, 0) or 0)
    rows = [(count, pattern) for pattern, count in totals.items() if count >= GROWTH_THRESHOLD and pattern not in mastered]
    rows.sort(key=lambda row: (-row[0], row[1]))
    return rows, totals


def _make_offer(pattern, evidence):
    token = uuid.uuid4().hex[:8]
    options = []
    for raw in ACHIEVEMENT_OPTIONS[pattern]:
        option = dict(raw)
        option["pattern"] = pattern
        option["id"] = _achievement_id(pattern, option["mechanic"])
        options.append(option)
    return {
        "token": token,
        "pattern": pattern,
        "pattern_label": PATTERNS[pattern],
        "evidence": list(evidence or [])[-3:],
        "options": options,
    }


def _archive_growth(campaign, dnd_module, session, original_archive, finale, epilogue):
    chat_before = campaign._chat_history(session.chat_id, True)
    old_rows = copy.deepcopy(chat_before.get("players") or {})

    original_archive(dnd_module, session, finale, epilogue)

    chat = campaign._chat_history(session.chat_id, True)
    players = chat.setdefault("players", {})
    created = []
    for participant in (getattr(session, "participants", {}) or {}).values():
        user_id = int(participant["user_id"])
        key = str(user_id)
        old = old_rows.get(key) or {}
        row = players.setdefault(key, {})

        rows, totals = _eligible_patterns(old, session, user_id)
        row["growth_progress"] = totals

        old_evidence = copy.deepcopy(old.get("growth_evidence") or {})
        current_evidence = (getattr(session, "growth_evidence", {}) or {}).get(key) or {}
        for pattern, items in current_evidence.items():
            merged = list(old_evidence.get(pattern) or []) + list(items or [])
            old_evidence[pattern] = merged[-8:]
        row["growth_evidence"] = old_evidence

        achievements = []
        session_achievements = (getattr(session, "learned_achievements", {}) or {}).get(key) or []
        source_achievements = session_achievements or old.get("achievements") or []
        for raw in source_achievements:
            item = _normalise_achievement(raw)
            if item and item["id"] not in {x["id"] for x in achievements}:
                achievements.append(item)
        row["achievements"] = achievements[:MAX_ACHIEVEMENTS]
        row["mastered_growth_patterns"] = list(old.get("mastered_growth_patterns") or [])

        pending = old.get("pending_achievement")
        if row.get("dead"):
            row.pop("pending_achievement", None)
        elif isinstance(pending, dict):
            row["pending_achievement"] = pending
        elif len(row["achievements"]) < MAX_ACHIEVEMENTS and rows:
            _, pattern = rows[0]
            evidence = old_evidence.get(pattern) or []
            offer = _make_offer(pattern, evidence)
            row["pending_achievement"] = offer
            created.append((user_id, offer["token"]))
        else:
            row.pop("pending_achievement", None)

    campaigns = chat.get("campaigns") or []
    if campaigns:
        campaigns[-1]["growth_counts"] = copy.deepcopy(getattr(session, "growth_counts", {}) or {})
        campaigns[-1]["growth_evidence"] = copy.deepcopy(getattr(session, "growth_evidence", {}) or {})
    campaign._save_archive(dnd_module)
    session.growth_created_offer_tokens = created


def _offer_keyboard(token):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="1", callback_data=f"dnd:ach:{token}:0"),
            InlineKeyboardButton(text="2", callback_data=f"dnd:ach:{token}:1"),
        ]]
    )


def _offer_text(row, offer):
    name = row.get("name") or "Герой"
    evidence = offer.get("evidence") or []
    evidence_text = ""
    if evidence:
        evidence_text = "\nЗа что: " + " / ".join(str(x) for x in evidence[-2:])
    options = offer.get("options") or []
    option_lines = [
        f"{index + 1}. 🏅 {option.get('title')} — {option.get('description')}"
        for index, option in enumerate(options[:2])
    ]
    return (
        f"🌱 {name} действительно выработал привычку: {offer.get('pattern_label')}."
        f"{evidence_text}\n\nВыбери, во что это превратится:\n" + "\n".join(option_lines)
    )


def _find_pending_offer(campaign, chat_id, user_id, token):
    history = campaign._player_history(chat_id, user_id)
    if not isinstance(history, dict):
        return None, None
    pending = history.get("pending_achievement")
    if not isinstance(pending, dict) or str(pending.get("token")) != str(token):
        return history, None
    return history, pending


def select_achievement(campaign, dnd, chat_id, user_id, token, option_index):
    history, offer = _find_pending_offer(campaign, chat_id, user_id, token)
    if not offer:
        return None, "Этот выбор уже закрыт."
    options = offer.get("options") or []
    if option_index not in {0, 1} or option_index >= len(options):
        return None, "Вариант потерялся."
    selected = _normalise_achievement(options[option_index])
    if selected is None:
        return None, "Механика варианта повреждена."

    achievements = []
    for raw in history.get("achievements") or []:
        item = _normalise_achievement(raw)
        if item and item["id"] != selected["id"]:
            achievements.append(item)
    achievements.append(selected)
    history["achievements"] = achievements[-MAX_ACHIEVEMENTS:]
    mastered = set(history.get("mastered_growth_patterns") or [])
    mastered.add(str(offer.get("pattern") or ""))
    history["mastered_growth_patterns"] = sorted(x for x in mastered if x)
    history.pop("pending_achievement", None)
    campaign._save_archive(dnd)

    session = (getattr(dnd, "dnd_sessions", {}) or {}).get(int(chat_id))
    if session is not None and str(int(user_id)) in (getattr(session, "participants", {}) or {}):
        _ensure(session)
        key = str(int(user_id))
        current = []
        for raw in session.learned_achievements.get(key, []) or []:
            item = _normalise_achievement(raw)
            if item and item["id"] != selected["id"]:
                current.append(item)
        selected_live = dict(selected)
        selected_live["charges_remaining"] = 1
        current.append(selected_live)
        session.learned_achievements[key] = current[-MAX_ACHIEVEMENTS:]
        dnd.persist_dnd_sessions()
    return selected, None


async def _achievement_callback(callback, dnd, campaign):
    parts = str(callback.data or "").split(":")
    if len(parts) != 4 or callback.message is None:
        await callback.answer("Кнопка протухла.", show_alert=True)
        return
    token = parts[2]
    try:
        option_index = int(parts[3])
    except (TypeError, ValueError):
        await callback.answer("Кнопка протухла.", show_alert=True)
        return

    chat_id = int(callback.message.chat.id)
    user_id = int(callback.from_user.id)
    selected, error = select_achievement(
        campaign,
        dnd,
        chat_id,
        user_id,
        token,
        option_index,
    )
    if selected is None:
        await callback.answer(error or "Этот выбор уже закрыт.", show_alert=True)
        return

    history = campaign._player_history(chat_id, user_id) or {}
    await callback.answer("Выбрано.")
    try:
        await callback.message.edit_text(
            f"🏅 {history.get('name') or callback.from_user.first_name}: «{selected['title']}»\n"
            f"{selected['description']}."
        )
    except Exception:
        await callback.message.answer(
            f"🏅 Получено достижение «{selected['title']}»: {selected['description']}."
        )


def _normalise_title(value):
    return " ".join(re.findall(r"[a-zа-яё0-9]+", str(value or "").casefold().replace("ё", "е")))


def _parse_achievement_use(session, user_id, text):
    _ensure(session)
    normalized = " ".join(str(text or "").strip().casefold().split())
    remainder = None
    for prefix in _USE_PREFIXES:
        if normalized.startswith(prefix):
            remainder = normalized[len(prefix):].strip()
            break
    if not remainder:
        return None

    candidates = []
    items = session.learned_achievements.get(str(int(user_id)), []) or []
    for index, item in enumerate(items):
        title = _normalise_title(item.get("title"))
        probe = _normalise_title(remainder)
        if probe == title or probe.startswith(title + " "):
            candidates.append((len(title), index, item))
    if not candidates:
        return None
    _, index, item = max(candidates, key=lambda row: row[0])
    return {
        "index": index,
        "id": item.get("id"),
        "title": item.get("title"),
        "mechanic": item.get("mechanic"),
        "text": str(text or "").strip(),
    }


def _active_progress_clock(session):
    from AI import dnd_scene_clocks as clocks

    clocks._ensure(session)
    for row in session.scene_clocks.values():
        if str(row.get("kind") or "").upper() == "PROGRESS" and not row.get("full"):
            return row
    return None


def _active_danger_clock(session):
    from AI import dnd_scene_clocks as clocks

    clocks._ensure(session)
    for row in session.scene_clocks.values():
        if str(row.get("kind") or "").upper() == "DANGER" and not row.get("full"):
            return row
    return None


def _prevalidate_achievement_use(session, user_id, plan):
    if not isinstance(plan, dict):
        return False, "Не смог распознать достижение.", None
    _ensure(session)
    items = session.learned_achievements.get(str(int(user_id)), []) or []
    try:
        item = items[int(plan["index"])]
    except (KeyError, IndexError, TypeError, ValueError):
        return False, "Достижение уже изменилось.", None
    if str(item.get("id")) != str(plan.get("id")):
        return False, "Достижение уже изменилось.", None
    if int(item.get("charges_remaining", 0) or 0) <= 0:
        return False, f"«{item.get('title')}» уже использовано в этом приключении.", None
    mechanic = str(item.get("mechanic") or "").upper()
    if mechanic == "CLOCK_PUSH" and _active_progress_clock(session) is None:
        return False, "Сейчас нет подходящей шкалы прогресса.", None
    if mechanic == "DANGER_REDUCE":
        threat = getattr(session, "threat", None)
        if _active_danger_clock(session) is None and not (isinstance(threat, dict) and threat.get("name") and int(threat.get("level", 0) or 0) > 0):
            return False, "Сейчас нет активной опасности, которую можно снизить.", None
    return True, None, dict(plan)


def _achievement_note(plan):
    mechanic = str(plan.get("mechanic") or "")
    labels = {
        "PARLEY": "партия получает одну реальную возможность переговоров до вражеской атаки",
        "ADVANTAGE_MOVE": "следующая подходящая проверка движения получает преимущество",
        "ADVANTAGE_SOCIAL": "следующая подходящая социальная проверка получает преимущество",
        "ADVANTAGE_PERCEPTION": "следующая подходящая проверка поиска получает преимущество",
        "ADVANTAGE_COMBAT": "следующая подходящая боевая проверка получает преимущество",
        "DANGER_REDUCE": "активная опасность снижается на 1",
        "CLOCK_PUSH": "подходящая шкала прогресса сдвигается на +2",
    }
    return f"КОДОВОЕ ДОСТИЖЕНИЕ «{plan.get('title')}»: {labels.get(mechanic, mechanic)}."


def _append_achievement_note(action, plan):
    return str(action or "").rstrip() + "\n" + _achievement_note(plan)


def _achievement_prompt(session):
    rows = []
    for player, plan in (getattr(session, "pending_achievement_uses", {}) or {}).items():
        if isinstance(plan, dict):
            rows.append(f"- ID {player}: {_achievement_note(plan)}")
    return "\n\n".join(["КОДОВЫЕ ДОСТИЖЕНИЯ В ЭТОМ ХОДЕ:", "\n".join(rows)]) if rows else ""


def _spend_achievement(session, user_id, plan):
    items = session.learned_achievements.get(str(int(user_id)), []) or []
    try:
        item = items[int(plan["index"])]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if str(item.get("id")) != str(plan.get("id")) or int(item.get("charges_remaining", 0) or 0) <= 0:
        return None
    item["charges_remaining"] = 0
    return item


def _reduce_danger(session, title):
    from AI import dnd_scene_clocks as clocks

    row = _active_danger_clock(session)
    if row is not None:
        return clocks._delta_clock(
            session,
            {"ID": row.get("id"), "DELTA": "-1", "CAUSE": f"достижение «{title}»"},
        )
    threat = getattr(session, "threat", None)
    if isinstance(threat, dict) and threat.get("name"):
        old = int(threat.get("level", 0) or 0)
        if old > 0:
            threat["level"] = old - 1
            return f"🚨 «{title}»: {threat.get('name')} снижается до {old - 1}/{threat.get('max', 6)}."
    return None


def _commit_achievement_use(session, user_id, plan):
    from AI import dnd_scene_clocks as clocks

    item = _spend_achievement(session, user_id, plan)
    if item is None:
        return []
    mechanic = str(item.get("mechanic") or "").upper()
    title = item.get("title")
    notices = [f"🏅 «{title}» использовано. Заряд 0/1."]

    if mechanic in _ADVANTAGE_DOMAIN:
        session.achievement_boosts[str(int(user_id))] = {
            "domain": _ADVANTAGE_DOMAIN[mechanic],
            "source": title,
        }
    elif mechanic == "PARLEY":
        session.achievement_world_facts = [
            fact for fact in session.achievement_world_facts
            if str(fact.get("kind") or "").upper() != "PARLEY"
        ]
        session.achievement_world_facts.append({
            "kind": "PARLEY",
            "player": int(user_id),
            "text": f"«{title}»: до следующей вражеской атаки партия обязана получить одно окно переговоров.",
        })
        notices.append(f"🗣 «{title}»: у партии есть окно переговоров до атаки.")
    elif mechanic == "DANGER_REDUCE":
        notice = _reduce_danger(session, title)
        if notice:
            notices.append(notice)
    elif mechanic == "CLOCK_PUSH":
        row = _active_progress_clock(session)
        if row is not None:
            notice = clocks._delta_clock(
                session,
                {"ID": row.get("id"), "DELTA": "2", "CAUSE": f"достижение «{title}»"},
            )
            if notice:
                notices.append(notice)
    return notices


def commit_pending_achievement_uses(session):
    _ensure(session)
    notices = []
    for player, plan in list(session.pending_achievement_uses.items()):
        try:
            user_id = int(player)
        except (TypeError, ValueError):
            continue
        if isinstance(plan, dict):
            notices.extend(_commit_achievement_use(session, user_id, plan))
    session.pending_achievement_uses = {}
    return notices


def _mode(suffix):
    match = _MODE_RE.search(";" + str(suffix or "").strip(";") + ";")
    value = match.group(1).upper() if match else "NORMAL"
    return value if value in {"NORMAL", "ADVANTAGE", "DISADVANTAGE"} else "NORMAL"


def _domain(action, suffix):
    if action in {"PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
        return "COMBAT"
    match = _DOMAIN_RE.search(";" + str(suffix or "").strip(";") + ";")
    return match.group(1).upper() if match else "OTHER"


def _set_mode(response, mode):
    text = str(response or "")
    match = _ACTION_RE.search(text)
    if not match:
        return text
    action = match.group(1).upper()
    suffix = (match.group(2) or "").strip(";")
    normalized = f";{suffix}" if suffix else ""
    existing = re.search(r";MODE:[A-Z_]+(?=;|$)", normalized, re.I)
    if existing:
        normalized = normalized[:existing.start()] + f";MODE:{mode}" + normalized[existing.end():]
    else:
        normalized += f";MODE:{mode}"
    return text[:match.start()] + f"[ACTION:{action}{normalized}]" + text[match.end():]


def _combine_advantage(mode):
    return "NORMAL" if str(mode).upper() == "DISADVANTAGE" else "ADVANTAGE"


def apply_achievement_boost(session, response):
    _ensure(session)
    match = _ACTION_RE.search(str(response or ""))
    if not match or match.group(1).upper() not in {"ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
        return str(response or ""), None
    action, suffix = match.group(1).upper(), match.group(2) or ""
    target_match = _TARGETS_RE.search(";" + suffix.strip(";") + ";")
    targets = [int(value) for value in target_match.group(1).split(",") if value.strip().isdigit()] if target_match else []
    if len(targets) != 1:
        return str(response or ""), None
    player = str(targets[0])
    boost = session.achievement_boosts.get(player)
    if not isinstance(boost, dict) or str(boost.get("domain") or "").upper() != _domain(action, suffix):
        return str(response or ""), None
    guarded = _set_mode(response, _combine_advantage(_mode(suffix)))
    source = boost.get("source")
    session.achievement_boosts.pop(player, None)
    return guarded, source


def _has_parley(session):
    return any(str(fact.get("kind") or "").upper() == "PARLEY" for fact in (getattr(session, "achievement_world_facts", []) or []))


def _consume_parley(session):
    session.achievement_world_facts = [
        fact for fact in (getattr(session, "achievement_world_facts", []) or [])
        if str(fact.get("kind") or "").upper() != "PARLEY"
    ]


def _block_attack_for_parley(response):
    text = str(response or "")
    match = _ACTION_RE.search(text)
    if not match or match.group(1).upper() != "ENEMY_ATTACK":
        return text, False
    return text[:match.start()] + "[ACTION:INPUT]" + text[match.end():], True


def apply_achievement_fact_metadata(session, original_text, cleaned, notices):
    _ensure(session)
    for match in _ACH_FACT_RE.finditer(str(original_text or "")):
        head, fields = _parse_fields(match.group(1))
        if head == "RESOLVE" and str(fields.get("KIND") or "").upper() == "PARLEY":
            _consume_parley(session)
    return _ACH_FACT_RE.sub("", str(cleaned or "")).strip(), notices


def _format_achievement_lines(items):
    lines = []
    for item in items or []:
        row = _normalise_achievement(item)
        if row:
            lines.append(f"🏅 {row['title']} — {row['description']}")
    return lines


class AchievementUseMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from AI import dnd
        from AI.dnd_any_bot_reply import is_any_bot_action_reply

        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is None or user is None:
            return await handler(event, data)
        session = dnd.dnd_sessions.get(int(chat.id))
        if not session or not dnd._is_participant_mode(session) or getattr(session, "state", None) != "WAITING_ACTION":
            return await handler(event, data)

        is_action_reply = dnd._is_group_action_reply(event) or is_any_bot_action_reply(event)
        if not is_action_reply:
            return await handler(event, data)

        _ensure(session)
        user_id = int(user.id)
        text = getattr(event, "text", None) or getattr(event, "caption", None) or ""
        plan = _parse_achievement_use(session, user_id, text)
        if plan is not None:
            ok, error, plan = _prevalidate_achievement_use(session, user_id, plan)
            if not ok:
                await event.answer(error or "Достижение сейчас неприменимо.")
                return None

        result = await handler(event, data)

        current = dnd.dnd_sessions.get(int(chat.id))
        if not current or getattr(current, "state", None) != "WAITING_ACTION":
            return result
        _ensure(current)
        key = str(user_id)
        action_row = (getattr(current, "pending_actions", {}) or {}).get(key)
        if not isinstance(action_row, dict):
            return result
        if plan is None:
            current.pending_achievement_uses.pop(key, None)
        else:
            current.pending_achievement_uses[key] = copy.deepcopy(plan)
            action_row["action"] = _append_achievement_note(action_row.get("action"), plan)
        dnd.persist_dnd_sessions()
        return result


def install_dnd_growth(dnd, dnd_router, *, state_policy, metadata_policy):
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(dnd, "_upupa_dnd_growth_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("growth_counts", lambda s: copy.deepcopy(getattr(s, "growth_counts", {}) or {}))
    state_policy.add_state_field("growth_evidence", lambda s: copy.deepcopy(getattr(s, "growth_evidence", {}) or {}))
    state_policy.add_state_field("growth_expected_actor_ids", lambda s: list(getattr(s, "growth_expected_actor_ids", []) or []))
    state_policy.add_state_field("growth_seen_scene_keys", lambda s: list(getattr(s, "growth_seen_scene_keys", []) or []))
    state_policy.add_state_field("learned_achievements", lambda s: copy.deepcopy(getattr(s, "learned_achievements", {}) or {}))
    state_policy.add_state_field("pending_achievement_uses", lambda s: copy.deepcopy(getattr(s, "pending_achievement_uses", {}) or {}))
    state_policy.add_state_field("achievement_boosts", lambda s: copy.deepcopy(getattr(s, "achievement_boosts", {}) or {}))
    state_policy.add_state_field("achievement_world_facts", lambda s: copy.deepcopy(getattr(s, "achievement_world_facts", []) or []))
    state_policy.add_restore_hook(_restore)

    metadata_policy.add_postprocessor(apply_growth_metadata)
    metadata_policy.add_postprocessor(apply_achievement_fact_metadata)

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + "\n" + _growth_context(session)

    campaign._campaign_context = campaign_context

    original_heritage = campaign._apply_heritage

    def apply_heritage(session, user_id, continuation=False):
        history = original_heritage(session, user_id, continuation=continuation)
        _load_achievements_from_history(session, user_id, history)
        return history

    campaign._apply_heritage = apply_heritage

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        return _archive_growth(campaign, dnd_module, session, original_archive, finale, epilogue)

    campaign._archive_campaign = archive_campaign

    original_finish = campaign._finish

    async def finish(dnd_module, bot, session, response):
        await original_finish(dnd_module, bot, session, response)
        chat = campaign._chat_history(session.chat_id)
        players = chat.get("players") or {}
        for user_id, token in list(getattr(session, "growth_created_offer_tokens", []) or []):
            row = players.get(str(int(user_id))) or {}
            pending = row.get("pending_achievement")
            if not isinstance(pending, dict) or str(pending.get("token")) != str(token):
                continue
            await bot.send_message(
                session.chat_id,
                _offer_text(row, pending),
                reply_markup=_offer_keyboard(token),
            )

    campaign._finish = finish

    original_generate = dnd.generate_session_response

    async def generate_session_response(session, prompt):
        _ensure(session)
        text = str(prompt or "")
        if "Игроки заявили действия одновременно:" in text:
            session.growth_expected_actor_ids = _group_actor_ids(session)
        actor_ids = list(session.growth_expected_actor_ids)
        if actor_ids:
            text += (
                "\n\n" + GROWTH_RULES
                + "\nРАЗРЕШАЕМЫЕ СЕЙЧАС ИГРОКИ: "
                + ", ".join(str(x) for x in actor_ids)
                + ". GROWTH-тег допустим только для этих ID."
            )
        if session.pending_achievement_uses and "Игроки заявили действия одновременно:" in text:
            text += "\n\n" + _achievement_prompt(session)

        result = await original_generate(session, text)

        if session.pending_achievement_uses and "Игроки заявили действия одновременно:" in text:
            notices = commit_pending_achievement_uses(session)
            dnd.persist_dnd_sessions()
            if notices:
                result = "\n".join(notices) + "\n\n" + str(result or "")
        return result

    dnd.generate_session_response = generate_session_response

    original_open = dnd.open_action_window

    async def open_action_window(bot, chat_id, target_user_ids=None):
        session = dnd.dnd_sessions.get(chat_id)
        if session:
            _ensure(session)
            session.pending_achievement_uses = {}
            if getattr(session, "state", None) != "WAITING_ROLL":
                session.growth_expected_actor_ids = []
        return await original_open(bot, chat_id, target_user_ids=target_user_ids)

    dnd.open_action_window = open_action_window

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        guarded = str(response or "")
        consume_parley_after = False
        if session and dnd._is_participant_mode(session):
            _ensure(session)
            action, targets = _targets_from_action(guarded)
            if action in {"ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"} and targets:
                session.growth_expected_actor_ids = targets

            guarded, boost_source = apply_achievement_boost(session, guarded)
            if boost_source:
                dnd.persist_dnd_sessions()

            if _has_parley(session):
                guarded, blocked = _block_attack_for_parley(guarded)
                consume_parley_after = True
                if blocked:
                    guarded = (
                        "🗣 Достижение не даёт врагу ударить первым: партия получает короткое окно переговоров.\n\n"
                        + guarded
                    )

        result = await original_parse(bot, chat_id, guarded)

        if session and dnd._is_participant_mode(session):
            if consume_parley_after:
                _consume_parley(session)
            action, targets = _targets_from_action(guarded)
            if action not in {"ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
                session.growth_expected_actor_ids = []
            dnd.persist_dnd_sessions()
        return result

    dnd.parse_and_execute_turn = parse_turn

    original_render_hero = state_commands.render_hero

    def render_hero(dnd_module, chat_id, user_id, user_name=None):
        text = original_render_hero(dnd_module, chat_id, user_id, user_name)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is not None:
            achievements = (getattr(session, "learned_achievements", {}) or {}).get(str(int(user_id)), []) or []
        else:
            history = campaign._player_history(chat_id, user_id) or {}
            achievements = history.get("achievements") or []
        lines = _format_achievement_lines(achievements)
        return text + (("\n\n🌱 Достижения\n" + "\n".join(lines)) if lines else "")

    state_commands.render_hero = render_hero

    dnd_router.message.outer_middleware(AchievementUseMiddleware())

    async def achievement_cb(callback):
        await _achievement_callback(callback, dnd, campaign)

    dnd_router.callback_query.register(achievement_cb, F.data.startswith("dnd:ach:"))
    dnd._upupa_dnd_growth_installed = True


__all__ = [
    "GROWTH_MARKER",
    "GROWTH_RULES",
    "GROWTH_THRESHOLD",
    "MAX_ACHIEVEMENTS",
    "PATTERNS",
    "ACHIEVEMENT_OPTIONS",
    "apply_growth_metadata",
    "commit_pending_achievement_uses",
    "apply_achievement_boost",
    "select_achievement",
    "install_dnd_growth",
]
