"""Structured long-term world and NPC memory for participant-mode Upupa DnD."""
from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timezone


WORLD_MEMORY_MARKER = "ДОЛГАЯ ПАМЯТЬ NPC DND УПУПЫ"
OBLIGATION_KINDS = {"NPC_OWES_PLAYERS", "PLAYERS_OWE_NPC", "MUTUAL", "NONE"}
_CLEAR_VALUES = {"", "-", "none", "нет", "ничего", "none."}

WORLD_MEMORY_RULES = f"""
{WORLD_MEMORY_MARKER}.
Для значимых NPC храни отдельно факт, эмоцию, долг и незакрытый хвост. Когда что-то существенно меняется, обновляй тот же NPC-тег:
[NPC:Капитан Ржа;EVENT:герои спасли его корабль;AFFECTED:123,456;ATTITUDE:благодарен, но всё ещё подозрителен;
OBLIGATION_KIND:NPC_OWES_PLAYERS;OBLIGATION:обещал один безопасный проход через порт;
WANTS:вернуть украденную карту;UNRESOLVED:герои обещали найти его матроса]
EVENT — что реально произошло. AFFECTED — ID игроков, которых это касается.
ATTITUDE — только эмоциональное отношение NPC, без долгов и обещаний.
OBLIGATION_KIND — только NPC_OWES_PLAYERS, PLAYERS_OWE_NPC, MUTUAL или NONE.
OBLIGATION — конкретный долг/обещанная услуга. WANTS — чего NPC сейчас добивается.
UNRESOLVED — конкретное незакрытое обещание, конфликт или вопрос.
Чтобы закрыть поле, передай NONE, например UNRESOLVED:NONE или OBLIGATION_KIND:NONE;OBLIGATION:NONE.
NOTE можно использовать для дополнительного краткого факта, как раньше.
Не меняй ATTITUDE просто потому, что у NPC появился долг: эмоция и обязательство независимы.

Если ниже есть КАНДИДАТ НА ВОЗВРАТ ИЗ ПРОШЛОГО, это максимум один архивный NPC на приключение.
Возвращай его только если это естественно для текущего сюжета. Если вернулся — его помощь, отказ, требование или конфликт должны
следовать из сохранённых ATTITUDE/OBLIGATION/WANTS/UNRESOLVED, а не из случайной ностальгии.
Положительный долг может привести к реальной помощи; отрицательный хвост — к требованию или проблеме.
Не вытаскивай других архивных NPC в эту партию. Новых NPC текущего приключения создавать можно без ограничений.
""".strip()


def _clean(value, limit=260):
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _clearable(value, limit=260):
    text = _clean(value, limit)
    return None if text.casefold() in _CLEAR_VALUES else text or None


def _safe_ids(value, valid_players=None):
    valid = {str(int(x)) for x in (valid_players or [])}
    result = []
    for token in re.findall(r"\d+", str(value or "")):
        normalized = str(int(token))
        if valid and normalized not in valid:
            continue
        if normalized not in result:
            result.append(normalized)
    return result[:12]


def normalize_npc(name, row=None):
    source = row if isinstance(row, dict) else {}
    npc_name = _clean(source.get("name") or name, 100) or _clean(name, 100) or "Неизвестный NPC"
    obligation_kind = str(source.get("obligation_kind") or "NONE").upper()
    if obligation_kind not in OBLIGATION_KINDS:
        obligation_kind = "NONE"
    affected = []
    for raw in source.get("affected_player_ids") or source.get("affected") or []:
        try:
            value = str(int(raw))
        except (TypeError, ValueError):
            continue
        if value not in affected:
            affected.append(value)
    return {
        "name": npc_name,
        "event": _clearable(source.get("event"), 260),
        "notes": [_clean(x, 220) for x in (source.get("notes") or []) if _clean(x, 220)][-12:],
        "attitude": _clearable(source.get("attitude"), 180),
        "obligation_kind": obligation_kind,
        "obligation": _clearable(source.get("obligation"), 220),
        "wants": _clearable(source.get("wants"), 220),
        "unresolved": _clearable(source.get("unresolved"), 220),
        "affected_player_ids": affected[:12],
        "updated_scene": int(source.get("updated_scene", 0) or 0),
        "last_seen_at": source.get("last_seen_at"),
        "last_seen_campaign": int(source.get("last_seen_campaign", 0) or 0),
        "callback_count": max(0, int(source.get("callback_count", 0) or 0)),
        "last_callback_campaign": int(source.get("last_callback_campaign", 0) or 0),
    }


def _ensure(session):
    if not isinstance(getattr(session, "world_callback_candidate", None), dict):
        session.world_callback_candidate = {}
    if not isinstance(getattr(session, "world_inherited_npc_keys", None), list):
        session.world_inherited_npc_keys = []
    session.world_callback_used = bool(getattr(session, "world_callback_used", False))


def _restore(session, data):
    row = data if isinstance(data, dict) else {}
    session.world_callback_candidate = copy.deepcopy(row.get("world_callback_candidate") or {})
    session.world_inherited_npc_keys = list(row.get("world_inherited_npc_keys") or [])
    session.world_callback_used = bool(row.get("world_callback_used", False))
    _ensure(session)


def _parse_fields(campaign, payload):
    return campaign._parse_fields(payload)


def _npc_key(value):
    return _clean(value, 100).casefold()


def _apply_field(item, field, raw):
    if field == "ATTITUDE":
        item["attitude"] = _clearable(raw, 180)
    elif field == "OBLIGATION":
        item["obligation"] = _clearable(raw, 220)
    elif field == "WANTS":
        item["wants"] = _clearable(raw, 220)
    elif field == "UNRESOLVED":
        item["unresolved"] = _clearable(raw, 220)


def apply_world_memory_metadata(campaign, session, original_text, cleaned, notices):
    _ensure(session)
    valid_players = {
        str(int(row.get("user_id", key)))
        for key, row in (getattr(session, "participants", {}) or {}).items()
        if str(row.get("user_id", key)).lstrip("-").isdigit()
    }
    for match in campaign.META_RE.finditer(str(original_text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "NPC":
            continue
        head, fields = _parse_fields(campaign, payload)
        if not head:
            continue
        key = _npc_key(head)
        base = normalize_npc(head, (getattr(session, "npc_memory", {}) or {}).get(key))
        if fields.get("EVENT"):
            base["event"] = _clearable(fields.get("EVENT"), 260)
        if fields.get("NOTE"):
            note = _clean(fields.get("NOTE"), 220)
            if note and (not base["notes"] or base["notes"][-1] != note):
                base["notes"] = (base["notes"] + [note])[-12:]
        for field in ("ATTITUDE", "OBLIGATION", "WANTS", "UNRESOLVED"):
            if field in fields:
                _apply_field(base, field, fields.get(field))
        if "OBLIGATION_KIND" in fields:
            obligation_kind = str(fields.get("OBLIGATION_KIND") or "").upper()
            if obligation_kind in OBLIGATION_KINDS:
                base["obligation_kind"] = obligation_kind
                if obligation_kind == "NONE":
                    base["obligation"] = None
        if "AFFECTED" in fields:
            base["affected_player_ids"] = _safe_ids(fields.get("AFFECTED"), valid_players)
        base["updated_scene"] = int(getattr(session, "scene_count", 0) or 0)
        session.npc_memory[key] = base

        inherited = [str(x) for x in session.world_inherited_npc_keys]
        if key in inherited:
            session.world_inherited_npc_keys = [x for x in inherited if x != key]

        candidate = session.world_callback_candidate or {}
        if key and key == _npc_key(candidate.get("name")):
            session.world_callback_used = True
    return cleaned, notices


def _format_npc_detail(row, *, include_notes=True):
    item = normalize_npc(row.get("name") if isinstance(row, dict) else "", row)
    parts = []
    if item["event"]:
        parts.append("факт: " + item["event"])
    if item["attitude"]:
        parts.append("отношение: " + item["attitude"])
    if item["obligation_kind"] != "NONE" and item["obligation"]:
        direction = {
            "NPC_OWES_PLAYERS": "должен героям",
            "PLAYERS_OWE_NPC": "герои должны",
            "MUTUAL": "взаимное обязательство",
        }.get(item["obligation_kind"], "обязательство")
        parts.append(f"{direction}: {item['obligation']}")
    if item["wants"]:
        parts.append("хочет: " + item["wants"])
    if item["unresolved"]:
        parts.append("не закрыто: " + item["unresolved"])
    if item["affected_player_ids"]:
        parts.append("касается ID " + ",".join(item["affected_player_ids"]))
    if include_notes and item["notes"]:
        parts.append("заметка: " + " / ".join(item["notes"][-2:]))
    return "; ".join(parts)


def render_npc_lines(memory, *, limit=12):
    values = [row for row in (memory or {}).values() if isinstance(row, dict)]
    lines = []
    for raw in values[-limit:]:
        item = normalize_npc(raw.get("name"), raw)
        detail = _format_npc_detail(item)
        lines.append(f"• {item['name']}" + (f" — {detail}" if detail else ""))
    if len(values) > limit:
        lines.append(f"…и ещё {len(values) - limit} персонажей в памяти.")
    return lines


def _filtered_npc_context(session):
    _ensure(session)
    inherited = set(str(x) for x in session.world_inherited_npc_keys)
    candidate_key = _npc_key((session.world_callback_candidate or {}).get("name"))
    rows = []
    for key, raw in list((getattr(session, "npc_memory", {}) or {}).items())[-16:]:
        normalized_key = _npc_key(key)
        if normalized_key in inherited and normalized_key != candidate_key:
            continue
        if not isinstance(raw, dict):
            continue
        item = normalize_npc(raw.get("name") or key, raw)
        detail = _format_npc_detail(item)
        rows.append(f"- {item['name']}: {detail or 'важных деталей пока нет'}")
    return "\n".join(rows[-8:]) or "- нет"


def _world_context(session):
    _ensure(session)
    candidate = session.world_callback_candidate or {}
    if not candidate:
        return "\n" + WORLD_MEMORY_RULES
    detail = _format_npc_detail(candidate)
    return (
        "\n"
        + WORLD_MEMORY_RULES
        + "\nКАНДИДАТ НА ВОЗВРАТ ИЗ ПРОШЛОГО: "
        + str(candidate.get("name") or "NPC")
        + (f" — {detail}" if detail else "")
        + "\nЭто не обязанность вводить его. Если он не подходит сцене, не упоминай его вообще."
    )


def _merge_world_npc(existing, incoming, *, explicit_fields=None):
    current = normalize_npc((existing or {}).get("name") if isinstance(existing, dict) else "", existing)
    fresh = normalize_npc((incoming or {}).get("name") if isinstance(incoming, dict) else "", incoming)
    explicit = {str(field) for field in (explicit_fields or set())}
    if fresh["name"]:
        current["name"] = fresh["name"]
    if fresh.get("event") is not None:
        current["event"] = fresh["event"]
    for field in ("attitude", "obligation", "wants", "unresolved"):
        if field in explicit:
            current[field] = fresh.get(field)
        elif fresh.get(field) is not None:
            current[field] = fresh[field]
    if "obligation_kind" in explicit:
        current["obligation_kind"] = fresh["obligation_kind"]
    elif fresh["obligation_kind"] != "NONE":
        current["obligation_kind"] = fresh["obligation_kind"]
    if "affected_player_ids" in explicit or "affected" in explicit:
        current["affected_player_ids"] = list(fresh["affected_player_ids"])
    elif fresh["affected_player_ids"]:
        merged = current["affected_player_ids"] + fresh["affected_player_ids"]
        current["affected_player_ids"] = list(dict.fromkeys(merged))[-12:]
    if fresh["notes"]:
        current["notes"] = (current["notes"] + fresh["notes"])[-12:]
    current["updated_scene"] = max(current["updated_scene"], fresh["updated_scene"])
    if fresh.get("last_seen_at"):
        current["last_seen_at"] = fresh["last_seen_at"]
    current["last_seen_campaign"] = max(current["last_seen_campaign"], fresh["last_seen_campaign"])
    current["callback_count"] = max(current["callback_count"], fresh["callback_count"])
    current["last_callback_campaign"] = max(current["last_callback_campaign"], fresh["last_callback_campaign"])
    return current


def backfill_world_npcs(archive):
    if not isinstance(archive, dict):
        return False
    chats = archive.get("chats")
    if not isinstance(chats, dict):
        return False
    changed = False
    for chat in chats.values():
        if not isinstance(chat, dict):
            continue
        if "world_npcs" not in chat:
            chat["world_npcs"] = {}
            changed = True
        world = chat["world_npcs"]
        campaigns = chat.get("campaigns") or []
        for index, campaign_row in enumerate(campaigns, 1):
            if not isinstance(campaign_row, dict):
                continue
            completed_at = campaign_row.get("completed_at")
            for key, raw in (campaign_row.get("npc_memory") or {}).items():
                if not isinstance(raw, dict):
                    continue
                incoming = normalize_npc(raw.get("name") or key, raw)
                incoming["last_seen_campaign"] = max(incoming["last_seen_campaign"], index)
                incoming["last_seen_at"] = incoming.get("last_seen_at") or completed_at
                npc_key = _npc_key(incoming["name"])
                merged = _merge_world_npc(
                    world.get(npc_key),
                    incoming,
                    explicit_fields=set(raw.keys()),
                )
                if world.get(npc_key) != merged:
                    world[npc_key] = merged
                    changed = True
    return changed


def _callback_score(row, campaign_count):
    item = normalize_npc(row.get("name") if isinstance(row, dict) else "", row)
    score = 0
    if item["unresolved"]:
        score += 6
    if item["obligation_kind"] != "NONE" and item["obligation"]:
        score += 5
    if item["wants"]:
        score += 3
    if item["attitude"]:
        score += 2
    if item["event"]:
        score += 1
    distance = max(0, campaign_count - int(item["last_callback_campaign"] or 0))
    score += min(3, distance)
    score -= min(4, item["callback_count"])
    if item["last_callback_campaign"] == campaign_count and campaign_count > 0:
        score -= 10
    return score


def _callback_candidates(campaign, chat_id, *, limit=6):
    chat = campaign._chat_history(chat_id, True)
    world = chat.get("world_npcs") or {}
    campaign_count = len(chat.get("campaigns") or [])
    rows = []
    for raw in world.values():
        if not isinstance(raw, dict):
            continue
        item = normalize_npc(raw.get("name"), raw)
        score = _callback_score(item, campaign_count)
        if score <= 0:
            continue
        rows.append((score, item["last_seen_campaign"], item["name"].casefold(), item))
    rows.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [row[-1] for row in rows[:limit]]


async def select_callback_candidate(campaign, dnd, session, plot, continuation=False):
    candidates = _callback_candidates(campaign, session.chat_id)
    if not candidates:
        return {}
    lines = [
        f"{index + 1}. {item['name']} — {_format_npc_detail(item)}"
        for index, item in enumerate(candidates)
    ]
    previous = campaign._latest_campaign(session.chat_id) if continuation else None
    prompt = (
        "Служебный выбор одного архивного NPC для возможного камбэка. Не пиши сюжет.\n"
        f"Новый сюжет: {plot}.\n"
        + (
            f"Предыдущий финал: {previous.get('finale') or ''}.\nПредыдущий эпилог: {previous.get('epilogue') or ''}.\n"
            if previous
            else ""
        )
        + "Выбери РОВНО ОДИН номер только если его прежняя связь естественно может повлиять на этот сюжет. "
        "Предпочитай незакрытые обещания, долги и текущие желания, а не просто знакомое имя. "
        "Если ни один не подходит — ответь только NONE.\nКАНДИДАТЫ:\n"
        + "\n".join(lines)
    )
    try:
        raw = str(await campaign._ephemeral_generate(dnd, session, prompt) or "").strip()
        if raw.upper().startswith("NONE"):
            return {}
        match = re.search(r"\b([1-6])\b", raw)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < len(candidates):
                return copy.deepcopy(candidates[index])
    except Exception:
        logging.exception("DnD world callback selection failed chat_id=%s", session.chat_id)

    # Conservative fallback: only auto-select when there is an explicit open thread.
    for item in candidates:
        if item.get("unresolved") or (item.get("obligation_kind") != "NONE" and item.get("obligation")):
            return copy.deepcopy(item)
    return {}


def _archive_world_memory(campaign, dnd_module, session, original_archive, finale, epilogue):
    original_archive(dnd_module, session, finale, epilogue)
    chat = campaign._chat_history(session.chat_id, True)
    campaigns = chat.get("campaigns") or []
    campaign_index = len(campaigns)
    completed_at = campaigns[-1].get("completed_at") if campaigns else datetime.now(timezone.utc).isoformat()
    world = chat.setdefault("world_npcs", {})

    for key, raw in (getattr(session, "npc_memory", {}) or {}).items():
        if not isinstance(raw, dict):
            continue
        item = normalize_npc(raw.get("name") or key, raw)
        item["last_seen_at"] = completed_at
        item["last_seen_campaign"] = campaign_index
        npc_key = _npc_key(item["name"])
        world[npc_key] = _merge_world_npc(
            world.get(npc_key),
            item,
            explicit_fields=set(raw.keys()),
        )

    candidate = getattr(session, "world_callback_candidate", {}) or {}
    if candidate:
        npc_key = _npc_key(candidate.get("name"))
        if npc_key in world and bool(getattr(session, "world_callback_used", False)):
            world[npc_key]["callback_count"] = int(world[npc_key].get("callback_count", 0) or 0) + 1
            world[npc_key]["last_callback_campaign"] = campaign_index

    if campaigns:
        campaigns[-1]["world_callback_candidate"] = candidate.get("name") if candidate else None
        campaigns[-1]["world_callback_used"] = bool(getattr(session, "world_callback_used", False))
    campaign._save_archive(dnd_module)


def install_dnd_world_memory(dnd, *, state_policy, metadata_policy):
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(dnd, "_upupa_dnd_world_memory_installed", False):
        return

    campaign._load_archive(dnd)
    if backfill_world_npcs(getattr(campaign, "_archive", None)):
        campaign._save_archive(dnd)

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field(
        "world_callback_candidate",
        lambda session: copy.deepcopy(getattr(session, "world_callback_candidate", {}) or {}),
    )
    state_policy.add_state_field(
        "world_inherited_npc_keys",
        lambda session: list(getattr(session, "world_inherited_npc_keys", []) or []),
    )
    state_policy.add_state_field(
        "world_callback_used",
        lambda session: bool(getattr(session, "world_callback_used", False)),
    )
    state_policy.add_restore_hook(_restore)

    metadata_policy.add_postprocessor(
        lambda session, original_text, cleaned, notices: apply_world_memory_metadata(
            campaign, session, original_text, cleaned, notices
        )
    )

    campaign._npc_context = _filtered_npc_context

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + _world_context(session)

    campaign._campaign_context = campaign_context

    original_start_story = campaign._start_story

    async def start_story(dnd_module, bot, session, plot, continuation=False, message=None):
        _ensure(session)
        latest = campaign._latest_campaign(session.chat_id) or {}
        session.world_inherited_npc_keys = (
            [_npc_key(key) for key in (latest.get("npc_memory") or {}).keys()]
            if continuation
            else []
        )
        session.world_callback_used = False
        session.world_callback_candidate = {}
        session.world_callback_candidate = await select_callback_candidate(
            campaign,
            dnd_module,
            session,
            plot,
            continuation=continuation,
        )
        dnd_module.persist_dnd_sessions()
        return await original_start_story(
            dnd_module,
            bot,
            session,
            plot,
            continuation=continuation,
            message=message,
        )

    campaign._start_story = start_story

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        return _archive_world_memory(
            campaign,
            dnd_module,
            session,
            original_archive,
            finale,
            epilogue,
        )

    campaign._archive_campaign = archive_campaign

    state_commands._format_npcs = render_npc_lines

    original_render_npcs = state_commands.render_npcs

    def render_npcs(dnd_module, chat_id):
        if dnd_module.dnd_sessions.get(int(chat_id)) is not None:
            return original_render_npcs(dnd_module, chat_id)
        campaign._load_archive(dnd_module)
        chat = campaign._chat_history(chat_id)
        world = chat.get("world_npcs") or {}
        if not world:
            return original_render_npcs(dnd_module, chat_id)
        lines = ["🤝 Связи", "Источник: долгая память мира по завершённым еграм."]
        lines.extend(render_npc_lines(world) or ["Пока ни одного сюжетного NPC не запомнили."])
        return "\n".join(lines)

    state_commands.render_npcs = render_npcs

    dnd._upupa_dnd_world_memory_installed = True


__all__ = [
    "WORLD_MEMORY_MARKER",
    "WORLD_MEMORY_RULES",
    "OBLIGATION_KINDS",
    "normalize_npc",
    "render_npc_lines",
    "backfill_world_npcs",
    "select_callback_candidate",
    "apply_world_memory_metadata",
    "install_dnd_world_memory",
]
