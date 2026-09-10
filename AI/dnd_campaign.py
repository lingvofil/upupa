"""Persistent campaign mechanics layered over the lightweight Upupa DnD engine."""
from __future__ import annotations

import difflib
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from aiogram import BaseMiddleware, F
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

MARKER = "КАМПАНИЯ УПУПЫ: ЖИВОЕ СОСТОЯНИЕ"
PROFILE_STEPS = ("style", "strength", "weakness", "special")
PROFILE_LABELS = {
    "style": "образ/стиль",
    "strength": "сильную сторону",
    "weakness": "слабость",
    "special": "особый приём",
}
PROFILE_OPTION_COUNT = 5
PROFILE_GENERATION_RETRIES = 3
PLOT_GENERATION_RETRIES = 3
RISK_DC = {"LOW": 8, "MEDIUM": 11, "HIGH": 14, "EXTREME": 17}
RISK_RU = {"LOW": "низкий", "MEDIUM": "средний", "HIGH": "высокий", "EXTREME": "крайний"}
THREAT_MAX = 6
META_RE = re.compile(r"\[(THREAT|NPC|ITEM|REP):([^\]]*)\]", re.I)
ACTION_RE = re.compile(r"\[ACTION:.*?\]", re.I | re.S)
OPTION_RE = re.compile(r"^\s*(?:\d+[.)]|[-•])\s*(.+?)\s*$")

RULES = f"""{MARKER}.
У участников есть лёгкий профиль: образ, сильная сторона, слабость, особый приём. Числовых статов и бонусов нет.
Сильная сторона может дать ADVANTAGE, слабость DISADVANTAGE, только когда это прямо относится к действию.
Особый приём — редкая характерная фишка, а не постоянный бонус.
Если бросок следует из выбранного игроком рискованного действия, добавляй RISK:LOW/MEDIUM/HIGH/EXTREME в ACTION:ROLL.
Код выставит сложность 8/11/14/17 соответственно. Не подгоняй DC ради результата.
Разные броски реально ветвят сюжет: запас успеха >=5 — сильный успех и новая возможность; успех — заявленное действие;
провал до 4 пунктов — действие не удалось с конкретной ценой; провал на 5+ — тяжёлое изменение ситуации.
Для текущей угрозы используй [THREAT:Подозрение стражи;DELTA:1;CAUSE:разбили витрину], DELTA обычно -1..1, редко 2.
Для вещей: [ITEM:ADD;PLAYER:123;NAME:бронзовый ключ;KIND:item] и [ITEM:REMOVE;PLAYER:123;NAME:бронзовый ключ].
Уникальные наследуемые вещи помечай KIND:artifact.
Память NPC: [NPC:Капитан Ржа;EVENT:обманули;NOTE:пообещали вернуть груз и сбежали].
Репутация: [REP:ADD;PLAYER:123;TEXT:известен как спаситель порта].
THREAT/NPC/ITEM/REP — скрытые служебные теги; ACTION-тег остаётся последним.
Не злоупотребляй четвёртой стеной, мастером игры, двойниками, слоями реальности и симуляциями. Это допустимо лишь если выбранный сюжет прямо мета-ориентирован или как редкий подготовленный поворот.
""".strip()

# These pools are never used during normal profile creation. They exist only as
# a last-resort emergency fallback after all AI attempts have failed.
EMERGENCY_PROFILE_OPTIONS = {
    "style": (
        "сонный контрабандист чудес", "светский охотник на проклятия", "дворовый алхимик-самоучка",
        "аристократ с плохими идеями", "бюрократ боевого назначения", "бродячий коллекционер неприятностей",
        "суеверный инженер катастроф", "театральный мошенник-идеалист", "курьер запретных реликвий",
        "провинциальный оккультист-практик", "герой поневоле в пальто", "язвительный следопыт-эстет",
    ),
    "strength": (
        "видит чужой блеф", "не теряется в бардаке", "договаривается с психами", "замечает мелкие несостыковки",
        "чинит всё из мусора", "умеет исчезнуть вовремя", "заражает остальных уверенностью", "мыслит неприятно нестандартно",
        "помнит бесполезные детали", "сохраняет лицо под давлением", "выкручивается на ходу", "чует опасные сделки",
    ),
    "weakness": (
        "лезет проверять запретное", "не умеет вовремя замолчать", "мстит по мелочам", "боится выглядеть трусом",
        "верит подозрительно красивым планам", "теряет голову от редкостей", "спорит с очевидным", "панически не любит тишину",
        "переоценивает собственный шарм", "залипает на загадках", "тащит домой странный хлам", "слишком любит эффектные выходы",
    ),
    "special": (
        "аварийный план из кармана", "убедительная наглая легенда", "показательно нелепый отвлекающий манёвр",
        "интуиция на одну катастрофу", "ритуал из подручного мусора", "последняя приличная идея",
        "блеф с каменным лицом", "неуместно точный бросок", "секунда нечеловеческого спокойствия",
        "внезапный союз с худшим кандидатом", "грязный трюк без инструкции", "героический поступок по ошибке",
    ),
}

# Technical emergency reserve only. Normal plot generation must succeed through AI.
EMERGENCY_PLOTS = (
    "Музей запахов запер посетителей внутри, а экспонаты начали охотиться на тех, кто их узнаёт.",
    "Плавучий рынок сорвало с якорей, и партия должна вернуть его до столкновения с военным портом.",
    "В горном санатории каждую ночь исчезает один этаж, но постояльцы упорно продолжают завтракать.",
    "Курьер привёз посылку, которая требует судебной защиты и утверждает, что внутри неё незаконно держат короля.",
    "На сельской ярмарке победитель конкурса гигантских овощей получает право командовать местной артиллерией.",
    "Археологи вскрыли древнюю прачечную, где потерянные носки за века построили воинственную цивилизацию.",
    "Воздушный цирк терпит крушение на крыше банка, и золото внезапно оказывается наименее ценной вещью внутри.",
    "В заброшенном аквапарке проснулся культ спасателей, поклоняющийся единственной работающей волновой машине.",
    "Городской оркестр случайно исполняет запрещённую мелодию, после которой архитектура начинает танцевать вместе с музыкой.",
    "На полярной станции изо льда вытаял ресторан, где счёт оплачивают воспоминаниями, а кухня требует реванша.",
    "Партии поручают сопроводить чрезвычайно вежливого монстра на конкурс красоты, пока конкуренты устраивают диверсии.",
    "Подземный почтамт объявляет забастовку, и неотправленные письма материализуют адресатов прямо в сортировочном зале.",
)

FORBIDDEN_PLOT_PATTERNS = (
    re.compile(r"\bврем\w*\s+петл\w*|\bпетл\w*\s+врем\w*|\bзацикл\w*.{0,30}\bврем\w*", re.I),
    re.compile(r"(?:ограб\w*|граб\w*).{0,60}(?:обув\w*|ботин\w*|туфл\w*|кроссов\w*)", re.I),
    re.compile(r"(?:обув\w*|ботин\w*|туфл\w*|кроссов\w*).{0,60}(?:ограб\w*|граб\w*)", re.I),
    re.compile(r"\bмарианск\w*", re.I),
    re.compile(r"(?:выбор\w*|избран\w*).{0,50}(?:мэр\w*|градоначальник\w*)", re.I),
    re.compile(r"(?:мэр\w*|градоначальник\w*).{0,50}(?:выбор\w*|избран\w*)", re.I),
)

ILLUSTRATION_STYLES = (
    "cinematic fantasy illustration",
    "pulp adventure poster",
    "dark comedy graphic novel",
    "absurdist adventure painting",
    "dynamic storybook action illustration",
)

_archive = {"version": 1, "chats": {}}
_archive_loaded = False


def _archive_path(dnd):
    p = Path(dnd.DND_STATE_PATH)
    return p.with_name(f"{p.stem}_campaigns.json")


def _load_archive(dnd):
    global _archive_loaded, _archive
    if _archive_loaded:
        return
    _archive_loaded = True
    try:
        p = _archive_path(dnd)
        data = json.loads(p.read_text("utf-8")) if p.exists() else None
        if isinstance(data, dict) and isinstance(data.get("chats"), dict):
            _archive = data
    except Exception:
        logging.exception("DnD campaign archive load failed")


def _save_archive(dnd):
    try:
        p = _archive_path(dnd)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(_archive, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(p)
    except Exception:
        logging.exception("DnD campaign archive save failed")


def _chat_history(chat_id, create=False):
    chats = _archive.setdefault("chats", {})
    key = str(int(chat_id))
    return chats.setdefault(key, {"players": {}, "campaigns": []}) if create else chats.get(key, {"players": {}, "campaigns": []})


def _player_history(chat_id, user_id):
    return (_chat_history(chat_id).get("players") or {}).get(str(int(user_id)))


def _latest_campaign(chat_id):
    rows = _chat_history(chat_id).get("campaigns") or []
    return rows[-1] if rows else None


def _ensure(session):
    defaults = {
        "character_profiles": {}, "profile_options": {}, "heritage": {}, "social_relationships": [], "inventories": {},
        "npc_memory": {}, "reputations": {}, "threat": {"name": None, "level": 0, "max": THREAT_MAX, "history": []},
        "plot_options": [], "selected_plot": None, "continuation_mode": False, "scene_log": [], "scene_count": 0,
        "next_illustration_at": random.randint(3, 5), "action_opened_at": None, "campaign_started_at": None,
    }
    for key, value in defaults.items():
        if not hasattr(session, key):
            setattr(session, key, value.copy() if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
    if not isinstance(session.threat, dict):
        session.threat = {"name": None, "level": 0, "max": THREAT_MAX, "history": []}
    for key, value in (("name", None), ("level", 0), ("max", THREAT_MAX), ("history", [])):
        session.threat.setdefault(key, value)
    if not isinstance(session.profile_options, dict):
        session.profile_options = {}


def _state(session):
    _ensure(session)
    return {key: getattr(session, key) for key in (
        "character_profiles", "profile_options", "heritage", "social_relationships", "inventories", "npc_memory", "reputations",
        "threat", "plot_options", "selected_plot", "continuation_mode", "scene_log", "scene_count",
        "next_illustration_at", "action_opened_at", "campaign_started_at",
    )}


def _restore_state(session, data):
    _ensure(session)
    if isinstance(data, dict):
        for key in _state(session):
            if key in data:
                setattr(session, key, data[key])
    _ensure(session)


def _profile_complete(profile):
    return bool(profile and all(profile.get(k) for k in PROFILE_STEPS))


def _emergency_random_profile():
    return {step: random.choice(EMERGENCY_PROFILE_OPTIONS[step]) for step in PROFILE_STEPS}


def _random_profile():
    """Backward-compatible name for the emergency-only procedural fallback."""
    return _emergency_random_profile()


def _profile_text(profile):
    return (f"образ — {profile.get('style')}; сильная сторона — {profile.get('strength')}; "
            f"слабость — {profile.get('weakness')}; особый приём — {profile.get('special')}")


def _clean_generated_value(value, *, max_chars=120):
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n\"'`*-—–")
    if not text or len(text) > max_chars:
        return None
    return text


def _extract_list_payload(raw):
    text = ACTION_RE.sub("", META_RE.sub("", str(raw or ""))).strip()
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    if fenced.startswith("["):
        try:
            data = json.loads(fenced)
            if isinstance(data, list):
                return [_clean_generated_value(item) for item in data]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    values = []
    for line in text.splitlines():
        match = OPTION_RE.match(line)
        if match:
            values.append(_clean_generated_value(match.group(1)))
    return values


def _normalized_text(value):
    return " ".join(re.findall(r"[a-zа-яё0-9]+", str(value or "").casefold().replace("ё", "е")))


def _text_similarity(left, right):
    a, b = _normalized_text(left), _normalized_text(right)
    if not a or not b:
        return 0.0
    sequence = difflib.SequenceMatcher(None, a, b).ratio()
    wa, wb = set(a.split()), set(b.split())
    union = wa | wb
    jaccard = len(wa & wb) / len(union) if union else 0.0
    return max(sequence, jaccard)


def _options_are_valid(options, *, expected=PROFILE_OPTION_COUNT, similarity_limit=0.82):
    if len(options) != expected or any(not item for item in options):
        return False
    normalized = [_normalized_text(item) for item in options]
    if len(set(normalized)) != expected:
        return False
    for index, item in enumerate(options):
        for other in options[index + 1:]:
            if _text_similarity(item, other) >= similarity_limit:
                return False
    return True


def _parse_profile_options(raw):
    return [item for item in _extract_list_payload(raw) if item]


def _parse_generated_profile(raw):
    text = ACTION_RE.sub("", META_RE.sub("", str(raw or ""))).strip()
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    data = None
    try:
        if fenced.startswith("{"):
            data = json.loads(fenced)
    except (TypeError, ValueError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        aliases = {
            "style": ("style", "образ", "стиль"),
            "strength": ("strength", "сильная сторона", "сила"),
            "weakness": ("weakness", "слабость", "слабая сторона"),
            "special": ("special", "особый приём", "особый прием", "приём", "прием"),
        }
        data = {}
        for line in text.splitlines():
            key, sep, value = line.partition(":")
            if not sep:
                continue
            normalized_key = _normalized_text(re.sub(r"^\s*\d+[.)]\s*", "", key))
            for canonical, names in aliases.items():
                if normalized_key in {_normalized_text(name) for name in names}:
                    data[canonical] = value
                    break
    profile = {step: _clean_generated_value(data.get(step), max_chars=100) for step in PROFILE_STEPS}
    return profile if _profile_complete(profile) else None


def _profile_keyboard(user_id, step, options):
    rows = [[InlineKeyboardButton(
        text=label,
        callback_data=f"dnd:prof:{int(user_id)}:{step}:{index}",
    )] for index, label in enumerate(options[:PROFILE_OPTION_COUNT])]
    rows.append([InlineKeyboardButton(
        text="🎲 Ещё варианты",
        callback_data=f"dnd:prof:{int(user_id)}:{step}:regen",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _heritage_keyboard(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♻️ Оставить прошлый образ", callback_data=f"dnd:prof:{user_id}:reuse")],
        [InlineKeyboardButton(text="🛠 Выбрать заново", callback_data=f"dnd:prof:{user_id}:edit")],
    ])


def _plot_keyboard(options, abstract=False):
    rows = [[InlineKeyboardButton(text=f"{i + 1}. {opt[:44]}", callback_data=f"dnd:plot:{i}")]
            for i, opt in enumerate(options[:5])]
    if abstract:
        rows.append([InlineKeyboardButton(text="✍️ Своя предыстория", callback_data="dnd:plot:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _apply_heritage(session, user_id, continuation=False):
    old = _player_history(session.chat_id, user_id)
    if not old:
        return None
    _ensure(session)
    key = str(int(user_id))
    session.heritage[key] = {
        "adventures": list(old.get("adventures") or [])[-6:],
        "reputation": list(old.get("reputation") or [])[-12:],
        "artifacts": list(old.get("artifacts") or []),
    }
    session.reputations[key] = list(old.get("reputation") or [])[-12:]
    items = old.get("inventory") if continuation else old.get("artifacts")
    if items:
        session.inventories[key] = list(items)
    return old


def _missing_profiles(session):
    _ensure(session)
    return [p.get("name") or str(p["user_id"]) for p in session.participants.values()
            if not _profile_complete(session.character_profiles.get(str(int(p["user_id"]))))]


def _profile_context(session):
    _ensure(session)
    out = []
    for key, profile in session.character_profiles.items():
        if _profile_complete(profile):
            name = session.participants.get(str(key), {}).get("name") or f"игрок {key}"
            out.append(f"- ID {key} {name}: {_profile_text(profile)}")
    return "\n".join(out) or "- нет"


def _inventory_context(session):
    _ensure(session)
    out = []
    for key, items in session.inventories.items():
        names = [x.get("name") if isinstance(x, dict) else str(x) for x in items]
        if names:
            out.append(f"- ID {key}: {', '.join(names)}")
    return "\n".join(out) or "- нет"


def _heritage_context(session):
    _ensure(session)
    out = []
    for key, data in session.heritage.items():
        bits = []
        adv = data.get("adventures") or []
        if adv:
            bits.append("прошлое: " + " / ".join(str(x.get("summary") or x.get("plot") or "")[:180] for x in adv[-2:]))
        if data.get("reputation"):
            bits.append("репутация: " + "; ".join(data["reputation"][-4:]))
        arts = [x.get("name") if isinstance(x, dict) else str(x) for x in data.get("artifacts") or []]
        if arts:
            bits.append("артефакты: " + ", ".join(arts))
        if bits:
            out.append(f"- ID {key}: " + " | ".join(bits))
    return "\n".join(out) or "- нет"


def _npc_context(session):
    _ensure(session)
    return "\n".join(f"- {v.get('name')}: {v.get('event')}; {' / '.join((v.get('notes') or [])[-3:])}"
                      for v in list(session.npc_memory.values())[-12:]) or "- нет"


def _campaign_context(dnd, session):
    _ensure(session)
    prefix = f"{MARKER}: актуальное состояние."
    if not dnd._is_participant_mode(session):
        return prefix
    relations = "\n".join(session.social_relationships) or "- данных мало"
    threat = session.threat
    threat_text = f"{threat.get('name')}: {threat.get('level')}/{threat.get('max')}" if threat.get("name") else "нет"
    return (f"{prefix}\nПРОФИЛИ:\n{_profile_context(session)}\nНАСЛЕДИЕ:\n{_heritage_context(session)}\n"
            f"ИНВЕНТАРЬ:\n{_inventory_context(session)}\nПАМЯТЬ NPC:\n{_npc_context(session)}\n"
            f"СОЦГРАФ (мягкий контекст, не истина):\n{relations}\nУГРОЗА: {threat_text}\n"
            + (f"СЮЖЕТ: {session.selected_plot}" if session.selected_plot else ""))


def _parse_fields(payload):
    parts = [x.strip() for x in payload.split(";") if x.strip()]
    fields = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep:
            fields[key.upper()] = value.strip()
    return (parts[0] if parts else ""), fields


def _bar(level, maximum=THREAT_MAX):
    level = max(0, min(maximum, int(level)))
    return "■" * level + "□" * (maximum - level)


def _apply_metadata(session, text):
    _ensure(session)
    notices = []
    valid_players = {str(int(x["user_id"])) for x in session.participants.values()} if getattr(session, "participants", None) else set()
    for match in META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        head, fields = _parse_fields(payload)
        if kind == "THREAT":
            name = head or session.threat.get("name") or "Угроза"
            try:
                delta = max(-2, min(2, int(fields.get("DELTA", 0))))
            except ValueError:
                delta = 0
            old = int(session.threat.get("level", 0)); maximum = int(session.threat.get("max", THREAT_MAX))
            new = max(0, min(maximum, old + delta)); session.threat.update(name=name, level=new)
            cause = fields.get("CAUSE") or "ситуация изменилась"
            if delta:
                session.threat.setdefault("history", []).append({"delta": delta, "cause": cause, "at": time.time()})
                notices.append(f"⚠️ {name}: {_bar(new, maximum)} {new}/{maximum} ({delta:+d}: {cause})")
        elif kind == "NPC" and head:
            item = session.npc_memory.setdefault(head.casefold(), {"name": head, "event": None, "notes": []})
            item["event"] = fields.get("EVENT") or item.get("event")
            if fields.get("NOTE"):
                item["notes"] = (item.get("notes") or [])[-11:] + [fields["NOTE"]]
        elif kind in {"ITEM", "REP"}:
            player = fields.get("PLAYER", "")
            if not player.isdigit() or (valid_players and player not in valid_players):
                continue
            name = session.participants.get(player, {}).get("name") or f"игрок {player}"
            if kind == "ITEM" and fields.get("NAME"):
                inv = session.inventories.setdefault(player, []); item_name = fields["NAME"]
                if head.upper() == "ADD" and not any((x.get("name") if isinstance(x, dict) else str(x)).casefold() == item_name.casefold() for x in inv):
                    inv.append({"name": item_name, "kind": fields.get("KIND", "item").lower()}); notices.append(f"🎒 {name} получает: {item_name}")
                elif head.upper() == "REMOVE":
                    session.inventories[player] = [x for x in inv if (x.get("name") if isinstance(x, dict) else str(x)).casefold() != item_name.casefold()]
                    notices.append(f"🎒 {name} теряет: {item_name}")
            elif kind == "REP" and head.upper() == "ADD" and fields.get("TEXT"):
                rep = session.reputations.setdefault(player, [])
                if fields["TEXT"] not in rep:
                    rep.append(fields["TEXT"]); session.reputations[player] = rep[-12:]; notices.append(f"🏷 {name}: {fields['TEXT']}")
    return META_RE.sub("", str(text or "")).strip(), notices


def _extract_risk(text):
    m = re.search(r"(?:^|;)RISK:(LOW|MEDIUM|HIGH|EXTREME)(?:;|$)", str(text), re.I)
    return m.group(1).upper() if m else None


def _risk_from_response(text):
    m = re.search(r"\[ACTION:ROLL;(.*?)\]", str(text or ""), re.I | re.S)
    return _extract_risk("ROLL;" + m.group(1)) if m else None


def _roll_grade_from_prompt(prompt):
    result = re.search(r"итог:\s*(\d+)", str(prompt), re.I)
    dc = re.search(r"Сложность:\s*(\d+)", str(prompt), re.I)
    if not result or not dc:
        return None
    margin = int(result.group(1)) - int(dc.group(1))
    if margin >= 5:
        return "сильный успех — дай дополнительную возможность или преимущество"
    if margin >= 0:
        return "успех — персонаж получает заявленное"
    if margin >= -4:
        return "провал с ценой — добавь конкретное осложнение"
    return "тяжёлый провал — заметно измени ситуацию в худшую сторону"


async def _ephemeral_generate(dnd, session, prompt):
    conversation = getattr(session, "conversation", None)
    before = len(conversation) if isinstance(conversation, list) else None
    try:
        return await dnd.generate_session_response(session, prompt)
    finally:
        if before is not None and isinstance(conversation, list) and len(conversation) > before:
            del conversation[before:]
        if dnd.dnd_sessions.get(session.chat_id) is session:
            dnd.persist_dnd_sessions()


def _profile_generation_prompt(session, user_id, step, exclude=None):
    profile = session.character_profiles.get(str(int(user_id)), {})
    participant = session.participants.get(str(int(user_id)), {})
    chosen = ", ".join(f"{PROFILE_LABELS[key]}: {profile[key]}" for key in PROFILE_STEPS if profile.get(key)) or "пока ничего"
    avoid = "; ".join(exclude or []) or "нет"
    return (
        "Служебная генерация лёгкого профиля персонажа Упупы. Это не игровой ход: никаких ACTION-тегов и сюжета.\n"
        f"Игрок: {participant.get('name') or user_id}. Уже выбрано: {chosen}.\n"
        f"Сейчас нужны варианты категории «{PROFILE_LABELS[step]}». Дай РОВНО {PROFILE_OPTION_COUNT} вариантов, по одному в строке 1–5.\n"
        "Каждый вариант — короткая выразительная фраза примерно 2–7 слов. Варианты должны заметно различаться по идее и тону, "
        "но сочетаться с уже выбранными чертами и собираться в цельного персонажа. Иногда можно быть смешным, странным и слегка абсурдным.\n"
        "Не используй числовые характеристики, классы, уровни, заклинательные списки и D&D-математику. "
        "Не зацикливайся на типовых архетипах и не повторяй формулировки из прошлой пачки.\n"
        f"Не повторяй сейчас: {avoid}. Только пять строк, без пояснений."
    )


async def _generate_profile_options(dnd, session, user_id, step, exclude=None):
    _ensure(session)
    last_error = None
    for attempt in range(PROFILE_GENERATION_RETRIES):
        try:
            raw = await _ephemeral_generate(dnd, session, _profile_generation_prompt(session, user_id, step, exclude))
            options = _parse_profile_options(raw)
            if _options_are_valid(options):
                session.profile_options.setdefault(str(int(user_id)), {})[step] = options
                if dnd.dnd_sessions.get(session.chat_id) is session:
                    dnd.persist_dnd_sessions()
                return options
            last_error = f"invalid options: {options!r}"
            logging.warning(
                "DnD profile options rejected chat_id=%s user_id=%s step=%s attempt=%s options=%r",
                session.chat_id, user_id, step, attempt + 1, options,
            )
        except Exception as exc:
            last_error = str(exc)
            logging.exception(
                "DnD profile option generation failed chat_id=%s user_id=%s step=%s attempt=%s",
                session.chat_id, user_id, step, attempt + 1,
            )
    pool = list(EMERGENCY_PROFILE_OPTIONS[step])
    excluded = {_normalized_text(item) for item in (exclude or [])}
    candidates = [item for item in pool if _normalized_text(item) not in excluded] or pool
    options = random.sample(candidates, k=min(PROFILE_OPTION_COUNT, len(candidates)))
    if len(options) < PROFILE_OPTION_COUNT:
        options.extend(item for item in pool if item not in options and len(options) < PROFILE_OPTION_COUNT)
    session.profile_options.setdefault(str(int(user_id)), {})[step] = options[:PROFILE_OPTION_COUNT]
    logging.error(
        "DnD profile options emergency fallback chat_id=%s user_id=%s step=%s cause=%s",
        session.chat_id, user_id, step, last_error,
    )
    if dnd.dnd_sessions.get(session.chat_id) is session:
        dnd.persist_dnd_sessions()
    return session.profile_options[str(int(user_id))][step]


async def _generate_complete_profile(dnd, session, user_id):
    participant = session.participants.get(str(int(user_id)), {})
    prompt = (
        "Служебная генерация лёгкого профиля персонажа Упупы для участника, который врывается в уже идущую партию. "
        "Это не игровой ход; не добавляй ACTION-теги.\n"
        f"Игрок: {participant.get('name') or user_id}. Создай ОДИН цельный, запоминающийся профиль. "
        "Черты должны логично сочетаться, но могут быть смешными или странными. Без классов, уровней, числовых статов и D&D-математики.\n"
        "Верни только JSON-объект с четырьмя строковыми полями: style, strength, weakness, special. "
        "Каждое значение короткое, примерно 2–7 слов."
    )
    last_error = None
    for attempt in range(PROFILE_GENERATION_RETRIES):
        try:
            profile = _parse_generated_profile(await _ephemeral_generate(dnd, session, prompt))
            if profile:
                return profile
            last_error = "invalid profile payload"
            logging.warning(
                "DnD complete profile rejected chat_id=%s user_id=%s attempt=%s",
                session.chat_id, user_id, attempt + 1,
            )
        except Exception as exc:
            last_error = str(exc)
            logging.exception(
                "DnD complete profile generation failed chat_id=%s user_id=%s attempt=%s",
                session.chat_id, user_id, attempt + 1,
            )
    logging.error(
        "DnD complete profile emergency fallback chat_id=%s user_id=%s cause=%s",
        session.chat_id, user_id, last_error,
    )
    return _emergency_random_profile()


async def _auto_profile(dnd, session, user_id):
    old = _apply_heritage(session, user_id, continuation=bool(session.continuation_mode)) or {}
    profile = dict(old.get("profile") or {})
    if not _profile_complete(profile):
        profile = await _generate_complete_profile(dnd, session, user_id)
    session.character_profiles[str(int(user_id))] = profile
    return profile


def _parse_plot_options(raw):
    return [item for item in _extract_list_payload(raw) if item]


def _plot_is_forbidden(plot):
    return any(pattern.search(plot) for pattern in FORBIDDEN_PLOT_PATTERNS)


def _plot_options_are_valid(options):
    if not _options_are_valid(options, expected=5, similarity_limit=0.78):
        return False
    return not any(_plot_is_forbidden(option) for option in options)


async def _social_context(session):
    try:
        from features.social_graph.analysis import aggregate_edges
        from features.social_graph.service import get_graph_data
        data = await get_graph_data(session.chat_id)
        ids = {int(x["user_id"]) for x in session.participants.values()}; lines = []
        for edge in sorted(aggregate_edges(data.interactions), key=lambda x: x.total_weight, reverse=True):
            if edge.user_a not in ids or edge.user_b not in ids:
                continue
            a = data.names.get(edge.user_a) or str(edge.user_a); b = data.names.get(edge.user_b) or str(edge.user_b)
            if edge.a_to_b > edge.b_to_a * 1.5:
                direction = f"{a} чаще тянется к {b}"
            elif edge.b_to_a > edge.a_to_b * 1.5:
                direction = f"{b} чаще тянется к {a}"
            else:
                direction = "взаимная связь"
            lines.append(f"- {a} ↔ {b}: {direction}")
            if len(lines) == 10:
                break
        session.social_relationships = lines
    except Exception:
        logging.exception("DnD social context failed chat_id=%s", session.chat_id)
        session.social_relationships = []


def _plot_generation_prompt(attempt):
    return (
        "Служебная генерация вариантов сюжета. Дай РОВНО 5 НОВЫХ сюжетов, по одной строке 1–5, до 18 слов каждый. "
        "Это не игровой ход: без ACTION-тегов и без пояснений.\n"
        "Пять вариантов обязаны заметно различаться одновременно сеттингом, масштабом, жанром и центральным конфликтом. "
        "Не делай пять вариаций одной идеи. Ищи неожиданные конкретные обстоятельства, а не абстрактную странность.\n"
        "КРИТИЧЕСКИ ВАЖНО: примеры из прежних инструкций задавали только степень свободы фантазии. "
        "Не копируй их, не перефразируй и не используй те же центральные сущности или сеттинги. "
        "Запрещены как центральная идея: временная петля, ограбление обувного магазина, Марианская впадина и выборы мэра.\n"
        "Также не скатывайся по умолчанию в симуляцию, альтернативную реальность, двойников, четвёртую стену или мастера игры внутри сюжета. "
        "Такие мета-тропы здесь лучше вообще не использовать.\n"
        f"Попытка генерации: {attempt}. Перед ответом молча проверь, что все пять задумок независимы друг от друга."
    )


async def _plot_choices(dnd, session):
    last_error = None
    for attempt in range(1, PLOT_GENERATION_RETRIES + 1):
        try:
            raw = await _ephemeral_generate(dnd, session, _plot_generation_prompt(attempt))
            options = _parse_plot_options(raw)
            if _plot_options_are_valid(options):
                return options
            last_error = f"invalid options: {options!r}"
            logging.warning(
                "DnD plot options rejected chat_id=%s attempt=%s options=%r",
                session.chat_id, attempt, options,
            )
        except Exception as exc:
            last_error = str(exc)
            logging.exception("DnD plot generation failed chat_id=%s attempt=%s", session.chat_id, attempt)
    logging.error("DnD plot emergency fallback chat_id=%s cause=%s", session.chat_id, last_error)
    return random.sample(list(EMERGENCY_PLOTS), 5)


def _lobby_text(session):
    _ensure(session)
    roster = "\n".join(("✅" if _profile_complete(session.character_profiles.get(str(p["user_id"]))) else "🧩") + " " + (p.get("name") or "Игрок") for p in session.participants.values()) or "Пока никто не записался."
    return (f"👥 Игра с участниками чата.\nВедущий: {session.starter_name}\n\nУчастники:\n{roster}\n\n"
            "После «Участвовать» выбери образ, сильную сторону, слабость и особый приём. Затем ведущий выбирает сюжет.")


def _lobby_keyboard(session=None):
    rows = [[InlineKeyboardButton(text="🙋 Участвовать", callback_data="dnd:lobby:join")],
            [InlineKeyboardButton(text="▶️ Выбрать сюжет", callback_data="dnd:lobby:start")]]
    if session is not None and _latest_campaign(session.chat_id):
        rows.append([InlineKeyboardButton(text="♻️ Продолжить прошлую кампанию", callback_data="dnd:lobby:continue")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _refresh_lobby(session, bot):
    if not session.lobby_message_id:
        return
    try:
        await bot.edit_message_text(chat_id=session.chat_id, message_id=session.lobby_message_id,
                                    text=_lobby_text(session), reply_markup=_lobby_keyboard(session))
    except Exception:
        pass


async def _show_profile_step(dnd, callback, session, user_id, step, *, regenerate=False):
    _ensure(session)
    current = list(session.profile_options.get(str(user_id), {}).get(step) or [])
    if regenerate or not _options_are_valid(current):
        current = await _generate_profile_options(dnd, session, user_id, step, exclude=current if regenerate else None)
    prefix = "🎲 Новая пачка. " if regenerate else "🎭 "
    await callback.message.edit_text(
        f"{prefix}Выбери {PROFILE_LABELS[step]}:",
        reply_markup=_profile_keyboard(user_id, step, current),
    )


async def _profile_prompt(dnd, callback, session):
    user_id = int(callback.from_user.id); _ensure(session)
    old = _apply_heritage(session, user_id) or {}; current = session.character_profiles.get(str(user_id))
    if _profile_complete(current):
        await callback.message.answer(f"🎭 {callback.from_user.first_name}: {_profile_text(current)}")
        await _refresh_lobby(session, callback.bot)
        return
    if _profile_complete(old.get("profile")):
        await callback.message.answer(
            f"🎭 {callback.from_user.first_name}, прошлый образ:\n{_profile_text(old['profile'])}\nОставляем или меняем?",
            reply_markup=_heritage_keyboard(user_id),
        )
    else:
        options = await _generate_profile_options(dnd, session, user_id, "style")
        await callback.message.answer(
            f"🎭 Выбери {PROFILE_LABELS['style']}:",
            reply_markup=_profile_keyboard(user_id, "style", options),
        )
    dnd.persist_dnd_sessions()


async def _profile_callback(callback, dnd):
    session = dnd.dnd_sessions.get(callback.message.chat.id) if callback.message else None
    if not session or session.mode != "participants" or session.state != "LOBBY":
        await callback.answer("Профиль уже не меняется.")
        return
    parts = str(callback.data).split(":")
    user_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    if int(callback.from_user.id) != user_id:
        await callback.answer("Это не твой персонаж.", show_alert=True)
        return
    if str(user_id) not in session.participants:
        await callback.answer("Сначала нажми «Участвовать».", show_alert=True)
        return
    action = parts[3] if len(parts) > 3 else ""
    if action == "reuse":
        old = _player_history(session.chat_id, user_id) or {}; profile = dict(old.get("profile") or {})
        if not _profile_complete(profile):
            await callback.answer("Старый профиль потерялся.")
            return
        session.character_profiles[str(user_id)] = profile
        session.profile_options.pop(str(user_id), None)
        dnd.persist_dnd_sessions()
        await callback.answer("Вернул.")
        await callback.message.edit_text("✅ " + _profile_text(profile))
        await _refresh_lobby(session, callback.bot)
        return
    if action == "edit":
        session.character_profiles[str(user_id)] = {}
        session.profile_options.pop(str(user_id), None)
        dnd.persist_dnd_sessions()
        await callback.answer("Генерирую.")
        options = await _generate_profile_options(dnd, session, user_id, "style")
        await callback.message.edit_text(
            f"🎭 Выбери {PROFILE_LABELS['style']}:",
            reply_markup=_profile_keyboard(user_id, "style", options),
        )
        return
    if len(parts) < 5 or action not in PROFILE_STEPS:
        await callback.answer("Кнопка протухла.")
        return
    token = parts[4]
    if token == "regen":
        await callback.answer("Перетряхиваю.")
        await _show_profile_step(dnd, callback, session, user_id, action, regenerate=True)
        return
    options = list(session.profile_options.get(str(user_id), {}).get(action) or [])
    if not _options_are_valid(options):
        # Sessions persisted by PR #485 may still expose the old hardcoded buttons
        # after a restart. There is no generated option batch to resolve their
        # numeric payload against, so migrate the current step in place.
        await callback.answer("Старая кнопка. Генерирую свежие варианты.")
        await _show_profile_step(dnd, callback, session, user_id, action, regenerate=False)
        return
    try:
        option_index = int(token)
        value = options[option_index]
    except (ValueError, IndexError):
        await callback.answer("Вариант пропал. Сгенерирую свежие.")
        await _show_profile_step(dnd, callback, session, user_id, action, regenerate=True)
        return
    profile = session.character_profiles.setdefault(str(user_id), {})
    profile[action] = value
    idx = PROFILE_STEPS.index(action)
    dnd.persist_dnd_sessions()
    await callback.answer("Записал.")
    if idx < len(PROFILE_STEPS) - 1:
        step = PROFILE_STEPS[idx + 1]
        next_options = await _generate_profile_options(dnd, session, user_id, step)
        await callback.message.edit_text(
            f"🎭 Теперь выбери {PROFILE_LABELS[step]}:",
            reply_markup=_profile_keyboard(user_id, step, next_options),
        )
    else:
        session.profile_options.pop(str(user_id), None)
        dnd.persist_dnd_sessions()
        await callback.message.edit_text("✅ Персонаж готов: " + _profile_text(profile))
        await _refresh_lobby(session, callback.bot)


async def _start_story(dnd, bot, session, plot, continuation=False, message=None):
    _ensure(session)
    session.selected_plot = plot
    session.continuation_mode = continuation
    session.campaign_started_at = datetime.now(timezone.utc).isoformat()
    session.state = "RESOLVING"
    for p in session.participants.values():
        uid = int(p["user_id"]); old = _apply_heritage(session, uid, continuation) or {}
        if not _profile_complete(session.character_profiles.get(str(uid))):
            inherited = dict(old.get("profile") or {})
            session.character_profiles[str(uid)] = inherited if _profile_complete(inherited) else await _generate_complete_profile(dnd, session, uid)
    session.profile_options = {}
    if continuation:
        old_campaign = _latest_campaign(session.chat_id) or {}; session.npc_memory = dict(old_campaign.get("npc_memory") or {})
        old_threat = old_campaign.get("threat") or {}; session.threat = {"name": old_threat.get("name"), "level": min(2, int(old_threat.get("level", 0))), "max": THREAT_MAX, "history": []}
    await _social_context(session); dnd.persist_dnd_sessions()
    if message:
        try:
            await message.edit_text("🎬 Сюжет выбран. Понеслась.")
        except Exception:
            pass
    recent = await dnd._collect_recent_chat_context(session.chat_id) or "Свежей переписки почти нет."
    previous = _latest_campaign(session.chat_id) if continuation else None
    prompt = (f"РЕЖИМ С УЧАСТНИКАМИ ЧАТА.\nУЧАСТНИКИ:\n{dnd._participants_prompt(session)}\nПРОФИЛИ:\n{_profile_context(session)}\n"
              f"СЮЖЕТ: {plot}\nСОЦГРАФ:\n" + ("\n".join(session.social_relationships) or "данных мало") +
              f"\nНАСЛЕДИЕ:\n{_heritage_context(session)}\nПЕРЕПИСКА:\n{recent}\n" +
              (f"ПРОШЛЫЙ ФИНАЛ: {previous.get('finale')}\nПРОШЛЫЙ ЭПИЛОГ: {previous.get('epilogue')}\n" if previous else "") +
              "Начни с конкретной проблемы; не пересказывай справку. Соцграф — только мягкий материал для отношений.")
    response = await dnd.generate_session_response(session, dnd.with_scene_direction(session, prompt))
    await dnd.parse_and_execute_turn(bot, session.chat_id, response)


async def _choose_plots(dnd, callback, session):
    session.plot_options = await _plot_choices(dnd, session)
    session.state = "WAITING_PLOT"
    dnd.persist_dnd_sessions()
    # Text and buttons are deliberately built from the exact same persisted array.
    options = session.plot_options
    text = "🎬 Выбери сюжет:\n\n" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(options))
    try:
        await callback.message.edit_text(text, reply_markup=_plot_keyboard(options, session.mode == "abstract"))
    except Exception:
        await callback.message.answer(text, reply_markup=_plot_keyboard(options, session.mode == "abstract"))


async def _plot_callback(callback, dnd):
    session = dnd.dnd_sessions.get(callback.message.chat.id) if callback.message else None
    if not session or session.state != "WAITING_PLOT":
        await callback.answer("Выбор протух.")
        return
    if not dnd._callback_is_host(callback, session):
        await callback.answer("Сюжет выбирает ведущий.", show_alert=True)
        return
    if callback.data == "dnd:plot:custom":
        session.state = "WAITING_BACKSTORY"; session.plot_options = []; dnd.persist_dnd_sessions(); await callback.answer(); await callback.message.edit_text("🎲 Своя предыстория.")
        msg = await callback.message.answer(f"Ладно, {session.starter_name}. Какую предысторию хочешь? (Ответь реплаем)"); session.backstory_prompt_message_id = msg.message_id; dnd.persist_dnd_sessions(); return
    try:
        plot = session.plot_options[int(str(callback.data).rsplit(":", 1)[1])]
    except (ValueError, IndexError):
        await callback.answer("Вариант пропал.")
        return
    await callback.answer("Погнали.")
    if session.mode == "participants":
        await _start_story(dnd, callback.bot, session, plot, message=callback.message)
        return
    session.selected_plot = plot; session.state = "RESOLVING"; dnd.persist_dnd_sessions(); await callback.message.edit_text("🎬 Сюжет выбран. Понеслась.")
    response = await dnd.generate_session_response(session, dnd.with_scene_direction(session, f"Выбранный сюжет: {plot}. Начинай с конкретной проблемы."))
    await dnd.parse_and_execute_turn(callback.bot, session.chat_id, response)


async def _continue_callback(callback, dnd):
    session = dnd.dnd_sessions.get(callback.message.chat.id) if callback.message else None
    if not session or session.state != "LOBBY":
        await callback.answer("Лобби закрыто.")
        return
    if not dnd._callback_is_host(callback, session):
        await callback.answer("Продолжение выбирает ведущий.", show_alert=True)
        return
    missing = _missing_profiles(session)
    if missing:
        await callback.answer("Не готовы: " + ", ".join(missing[:4]), show_alert=True)
        return
    if not _latest_campaign(session.chat_id):
        await callback.answer("Завершённой кампании нет.", show_alert=True)
        return
    await callback.answer("Поднимаю старые грехи.")
    await _start_story(dnd, callback.bot, session, "Продолжение прошлой кампании и её последствий", True, callback.message)


class CampaignCallbackMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from AI import dnd
        key = str(getattr(event, "data", "") or "")
        message = getattr(event, "message", None)
        session = dnd.dnd_sessions.get(message.chat.id) if message else None
        if key == "dnd:mode:abstract" and session and session.state == "WAITING_MODE":
            if not dnd._callback_is_host(event, session):
                await event.answer("Режим выбирает ведущий.", show_alert=True)
                return None
            session.mode = "abstract"; session.mode_prompt_message_id = None; await event.answer(); await _choose_plots(dnd, event, session); return None
        if key == "dnd:lobby:start" and session:
            missing = _missing_profiles(session)
            if missing:
                await event.answer("Не готовы: " + ", ".join(missing[:4]), show_alert=True)
                return None
        result = await handler(event, data)
        if key == "dnd:lobby:join" and session and session.state == "LOBBY":
            await _profile_prompt(dnd, event, session)
        return result


def _record_scene(session, text):
    story = ACTION_RE.sub("", META_RE.sub("", str(text or ""))).strip()
    if story:
        _ensure(session); session.scene_count += 1; session.scene_log = (session.scene_log + [story])[-24:]
    return story


async def _image(bot, chat_id, prompt, filename, caption):
    try:
        from features.image_generation import generate_image_bytes
        data, provider = await generate_image_bytes(prompt, log_context="dnd")
        if data:
            logging.info("[dnd] image provider=%s chat_id=%s", provider, chat_id)
            await bot.send_photo(chat_id, BufferedInputFile(data, filename=filename), caption=caption)
    except Exception:
        logging.exception("DnD image generation failed chat_id=%s", chat_id)


def _scene_image_prompt(session, scene, *, style=None):
    visual_style = style or random.choice(ILLUSTRATION_STYLES)
    return (
        "Create a vivid key-scene illustration for an absurd tabletop adventure. No text, captions, speech bubbles, UI or watermarks.\n"
        f"VISUAL STYLE: {visual_style}. Let the style serve this specific scene rather than forcing generic fantasy aesthetics.\n"
        f"EXACT CURRENT SCENE: {scene[:2200]}\n"
        f"CHARACTER PROFILES TO RESPECT: {_profile_context(session)[:1800]}\n"
        "Show one unmistakable central action or turning point with readable character poses and expressions. Preserve concrete objects, locations, "
        "actions and absurd details from the scene. If the moment is funny, make the visual joke legible through staging rather than text. "
        "Use strong composition, atmospheric lighting, depth, expressive faces, rich color and scene-specific detail. "
        "Do not default to a pale grey-beige pencil sketch, concept-art mush, or a generic fantasy village unless the scene explicitly contains one."
    )


def _final_comic_prompt(session, epilogue, *, style=None):
    visual_style = style or random.choice(ILLUSTRATION_STYLES)
    scenes = "\n---\n".join(session.scene_log[-6:])
    return (
        "Create a coherent four-panel comic showing four chronological beats from a finished absurd tabletop adventure. "
        "No text, captions, speech bubbles, UI or watermarks.\n"
        f"VISUAL STYLE: {visual_style}; keep character appearances consistent across panels.\n"
        f"CHARACTER PROFILES: {_profile_context(session)[:1800]}\n"
        f"REAL EVENTS, IN ORDER:\n{scenes[:3600]}\nEPILOGUE CONSEQUENCES: {epilogue[:900]}\n"
        "Each panel must depict a distinct real event from the supplied history, not invented filler. Use expressive action, visual comedy where appropriate, "
        "cinematic lighting and clear panel-to-panel progression. Avoid generic fantasy scenery unrelated to these events."
    )


def _maybe_image(dnd, bot, session, story):
    if session.scene_count < session.next_illustration_at:
        return
    session.next_illustration_at = session.scene_count + random.randint(3, 5)
    prompt = _scene_image_prompt(session, story)
    dnd._start_background_task(
        _image(bot, session.chat_id, prompt, "dnd_scene.png", "🖼 Ключевой кадр этой ебучей саги."),
        name=f"dnd-illustration:{session.chat_id}:{session.scene_count}",
    )


def _delay_threat(session):
    _ensure(session)
    if not session.action_opened_at or time.time() - float(session.action_opened_at) < 150 or not session.threat.get("name"):
        return None
    old = int(session.threat.get("level", 0)); new = min(int(session.threat.get("max", THREAT_MAX)), old + 1)
    if new == old:
        return None
    session.threat["level"] = new; session.threat.setdefault("history", []).append({"delta": 1, "cause": "промедление", "at": time.time()})
    return f"⏳ Пока вы чесались, {session.threat['name'].lower()} ухудшилась: {_bar(new)} {new}/{THREAT_MAX}."


def _archive_campaign(dnd, session, finale, epilogue):
    chat = _chat_history(session.chat_id, True); now = datetime.now(timezone.utc).isoformat()
    row = {"completed_at": now, "selected_plot": session.selected_plot, "finale": finale, "epilogue": epilogue,
           "profiles": session.character_profiles, "inventories": session.inventories, "npc_memory": session.npc_memory,
           "reputations": session.reputations, "threat": session.threat, "scenes": session.scene_log[-10:]}
    chat["campaigns"] = (chat.get("campaigns") or [])[-19:] + [row]
    players = chat.setdefault("players", {})
    for p in session.participants.values():
        key = str(int(p["user_id"])); old = players.get(key) or {}; inv = list(session.inventories.get(key) or [])
        adventures = (old.get("adventures") or []) + [{"completed_at": now, "plot": session.selected_plot, "summary": epilogue or finale}]
        players[key] = {"name": p.get("name"), "profile": dict(session.character_profiles.get(key) or old.get("profile") or {}),
                        "inventory": inv, "artifacts": [x for x in inv if isinstance(x, dict) and x.get("kind") == "artifact"],
                        "reputation": list(session.reputations.get(key) or old.get("reputation") or [])[-12:], "adventures": adventures[-6:]}
    _save_archive(dnd)


async def _finish(dnd, bot, session, response):
    clean, notices = _apply_metadata(session, response); finale = ACTION_RE.sub("", clean).strip(); _record_scene(session, finale)
    if finale:
        await bot.send_message(session.chat_id, finale + (("\n\n" + "\n".join(notices)) if notices else ""))
    try:
        ep = await dnd.generate_session_response(session, "История закончена. Дай эпилог 50–70 слов только по реальным решениям и последствиям. У каждого важного участника оставь конкретный хвост: судьба, репутация или артефакт. Без служебных тегов.")
        ep = ACTION_RE.sub("", META_RE.sub("", ep)).strip()
    except Exception:
        logging.exception("DnD epilogue failed")
        ep = ""
    if ep:
        await bot.send_message(session.chat_id, "🏁 Эпилог\n" + ep)
    _archive_campaign(dnd, session, finale, ep)
    comic_prompt = _final_comic_prompt(session, ep)
    chat_id = session.chat_id
    dnd.cleanup_session(chat_id)
    await bot.send_message(chat_id, "☠️ Егра окончена. Наследие этой катастрофы сохранено.")
    try:
        dnd._start_background_task(
            _image(bot, chat_id, comic_prompt, "dnd_final_comic.png", "📚 Финальный комикс. Вот до чего вы доигрались."),
            name=f"dnd-final-comic:{chat_id}:{int(time.time())}",
        )
    except Exception:
        # The story is already archived and cleaned up; image scheduling must never resurrect/fail the session.
        logging.exception("DnD final comic scheduling failed chat_id=%s", chat_id)


def _notices(text, notices):
    if not notices:
        return text
    m = ACTION_RE.search(text); body = ACTION_RE.sub("", text).strip() + "\n\n" + "\n".join(notices)
    return body + (("\n" + m.group(0)) if m else "")


def configure_dnd_campaign(dnd, router):
    if getattr(dnd, "_upupa_dnd_campaign_configured", False):
        return
    _load_archive(dnd)
    original_to_record = dnd.GameSession.to_record; original_from_record = dnd.GameSession.from_record.__func__

    def to_record(self):
        row = original_to_record(self); row["campaign_state"] = _state(self); return row

    @classmethod
    def from_record(cls, row):
        session = original_from_record(cls, row); _restore_state(session, (row or {}).get("campaign_state")); return session

    dnd.GameSession.to_record = to_record; dnd.GameSession.from_record = from_record
    dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.replace(
        "8. Не используй характеристики, модификаторы, бонусы персонажей или листы персонажей.\n   REASON описывает конкретное действие или опасность в текущей сцене.",
        "8. Не используй числовые характеристики, модификаторы, бонусы или полноценные листы. В режиме участников учитывай лёгкий профиль повествовательно и через уместные ADVANTAGE/DISADVANTAGE.\n   REASON описывает конкретное действие или опасность в текущей сцене.")
    dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + RULES

    original_generate = dnd.generate_session_response

    async def generate(session, prompt):
        _ensure(session); grade = _roll_grade_from_prompt(prompt)
        if grade:
            prompt += "\nГРАДАЦИЯ ИСХОДА: " + grade + ". Развивай сцену именно по этой ветке."
        before = len(session.conversation); result = await original_generate(session, prompt + "\n\n" + _campaign_context(dnd, session))
        for item in session.conversation[before:]:
            if isinstance(item, dict) and item.get("role") == "user":
                item["content"] = prompt
                break
        if dnd.dnd_sessions.get(session.chat_id) is session:
            dnd.persist_dnd_sessions()
        return result

    dnd.generate_session_response = generate

    original_parse_roll = dnd._parse_roll_command

    def parse_roll(command):
        roll = original_parse_roll(command); risk = _extract_risk(command)
        if risk:
            roll["risk"] = risk; roll["dc"] = RISK_DC[risk]
        return roll

    dnd._parse_roll_command = parse_roll
    dnd._lobby_text = _lobby_text; dnd._lobby_keyboard = lambda: _lobby_keyboard()

    async def start_participant(callback, session):
        missing = _missing_profiles(session)
        if missing:
            await callback.answer("Не готовы: " + ", ".join(missing[:4]), show_alert=True)
            return
        await _choose_plots(dnd, callback, session)

    dnd._start_participant_story = start_participant

    original_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if not session:
            return await original_parse_turn(bot, chat_id, response)
        if re.search(r"\[ACTION:END\]", str(response), re.I):
            return await _finish(dnd, bot, session, response)
        clean, notices = _apply_metadata(session, response); story = _record_scene(session, clean); risk = _risk_from_response(response)
        if risk:
            notices.append(f"🎚 Риск: {RISK_RU[risk]} → сложность {RISK_DC[risk]}.")
        dnd.persist_dnd_sessions(); await original_parse_turn(bot, chat_id, _notices(clean, notices))
        if story:
            _maybe_image(dnd, bot, session, story)
        dnd.persist_dnd_sessions()

    dnd.parse_and_execute_turn = parse_turn

    original_open = dnd.open_action_window

    async def open_action(bot, chat_id, target_user_ids=None):
        result = await original_open(bot, chat_id, target_user_ids=target_user_ids); session = dnd.dnd_sessions.get(chat_id)
        if session:
            _ensure(session); session.action_opened_at = time.time(); dnd.persist_dnd_sessions()
        return result

    dnd.open_action_window = open_action
    original_finalize = dnd.finalize_group_actions

    async def finalize(bot, chat_id, prompt_message_id):
        session = dnd.dnd_sessions.get(chat_id)
        if session:
            notice = _delay_threat(session); session.action_opened_at = None
            if notice:
                await bot.send_message(chat_id, notice); dnd.persist_dnd_sessions()
        return await original_finalize(bot, chat_id, prompt_message_id)

    dnd.finalize_group_actions = finalize

    from AI.dnd_completion import DndParticipantCompletionMiddleware
    old_precollect = DndParticipantCompletionMiddleware._precollect_action_reply

    async def precollect(self, dnd_module, bot, event):
        chat = getattr(event, "chat", None); user = getattr(event, "from_user", None); session = dnd_module.dnd_sessions.get(int(chat.id)) if chat else None
        uid = int(user.id) if user else None; was_new = bool(session and uid is not None and str(uid) not in session.participants)
        result = await old_precollect(self, dnd_module, bot, event); session = dnd_module.dnd_sessions.get(int(chat.id)) if chat else None
        if was_new and session and str(uid) in session.participants:
            profile = await _auto_profile(dnd_module, session, uid)
            dnd_module.persist_dnd_sessions()
            await bot.send_message(session.chat_id, f"🎭 {user.first_name} врывается сразу. Профиль выдан автоматически: {_profile_text(profile)}.")
        return result

    DndParticipantCompletionMiddleware._precollect_action_reply = precollect

    router.callback_query.outer_middleware(CampaignCallbackMiddleware())

    async def prof_cb(cb):
        await _profile_callback(cb, dnd)

    async def plot_cb(cb):
        await _plot_callback(cb, dnd)

    async def cont_cb(cb):
        await _continue_callback(cb, dnd)

    router.callback_query.register(prof_cb, F.data.startswith("dnd:prof:"))
    router.callback_query.register(plot_cb, F.data.startswith("dnd:plot:"))
    router.callback_query.register(cont_cb, F.data == "dnd:lobby:continue")
    dnd._upupa_dnd_campaign_configured = True
