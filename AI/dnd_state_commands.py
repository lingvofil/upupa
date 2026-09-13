"""Read-only player-facing access to persistent DnD campaign state."""
from __future__ import annotations

import logging
import re

from aiogram import BaseMiddleware


_STATE_ALIASES = {
    "hero": {"герой", "днд герой"},
    "inventory": {"инвентарь", "днд инвентарь"},
    "npcs": {"днд связи"},
    "status": {"днд сюжет"},
}
_START_ALIASES = {"упупа днд"}
_END_ALIASES = {"днд конец"}
_LEGACY_END_ALIASES = {"упупа заверши историю", "упупа закончи историю"}
_LOBBY_MENU_ALIASES = {"днд"}
_LOBBY_START_ALIASES = {"днд старт"}
_NPC_TAG_RE = re.compile(r"\[NPC:[^\]]*\]", re.I)

_STATE_LABELS = {
    "WAITING_MODE": "выбираем режим егры",
    "LOBBY": "собираем учаснегов",
    "WAITING_BACKSTORY": "ждём предысторию",
    "WAITING_PLOT": "ведущий выбирает сюжет",
    "RESOLVING": "мастер разгребает последствия",
    "WAITING_ACTION": "партия решает, что делать",
    "WAITING_ROLL": "ждём бросок",
    "WAITING_POLL": "идёт голосование",
}
_PROFILE_LABELS = {
    "style": "Образ",
    "strength": "Сильная сторона",
    "weakness": "Слабость",
    "special": "Особый приём",
}
_MODE_LABELS = {
    "NORMAL": "обычный",
    "ADVANTAGE": "с преимуществом",
    "DISADVANTAGE": "с помехой",
}


def _normalize_command(text: str | None) -> str:
    value = " ".join(str(text or "").strip().casefold().split())
    return value.rstrip(" ?!.,:;").strip()


def command_kind(text: str | None) -> str | None:
    normalized = _normalize_command(text)
    if normalized in _START_ALIASES:
        return "start"
    if normalized in _END_ALIASES:
        return "end"
    if any(normalized == alias or normalized.startswith(alias + " ") for alias in _LEGACY_END_ALIASES):
        return "legacy_end"
    if normalized in _LOBBY_START_ALIASES:
        return "lobby_start"
    if normalized in _LOBBY_MENU_ALIASES:
        return "lobby_menu"
    for kind, aliases in _STATE_ALIASES.items():
        if normalized in aliases:
            return kind
    return None


def is_state_command(text: str | None) -> bool:
    return command_kind(text) is not None


def _campaign_module(dnd):
    from AI import dnd_campaign

    dnd_campaign._load_archive(dnd)
    return dnd_campaign


def _active_session(dnd, chat_id: int):
    session = dnd.dnd_sessions.get(int(chat_id))
    if session is not None:
        _campaign_module(dnd)._ensure(session)
    return session


def _open_participant_lobby(dnd, chat_id: int):
    session = dnd.dnd_sessions.get(int(chat_id))
    if not session or getattr(session, "mode", None) != "participants" or getattr(session, "state", None) != "LOBBY":
        return None
    _campaign_module(dnd)._ensure(session)
    return session


async def _repost_lobby(event, dnd) -> None:
    session = _open_participant_lobby(dnd, int(event.chat.id))
    if session is None:
        await event.answer("Сейчас открытого лобби ДНД нет. Запусти его через «упупа днд».")
        return

    campaign = _campaign_module(dnd)
    prompt = await event.answer(
        campaign._lobby_text(session),
        reply_markup=campaign._lobby_keyboard(session),
    )
    session.lobby_message_id = prompt.message_id
    dnd.persist_dnd_sessions()


async def _start_lobby_from_message(event, dnd) -> None:
    session = _open_participant_lobby(dnd, int(event.chat.id))
    if session is None:
        await event.answer("Сейчас открытого лобби ДНД нет.")
        return

    user_id = int(event.from_user.id)
    starter_user_id = getattr(session, "starter_user_id", None)
    if starter_user_id is None or user_id != int(starter_user_id):
        await event.answer("Запустить игру может только ведущий.")
        return
    if not (getattr(session, "participants", {}) or {}):
        await event.answer("Сначала хотя бы кто-нибудь должен нажать «Участвовать».")
        return

    campaign = _campaign_module(dnd)
    missing = campaign._missing_profiles(session)
    if missing:
        await event.answer("Не готовы: " + ", ".join(missing[:4]))
        return

    session.plot_options = await campaign._plot_choices(dnd, session)
    session.state = "WAITING_PLOT"
    dnd.persist_dnd_sessions()
    options = session.plot_options
    text = "🎬 Выбери сюжет:\n\n" + "\n".join(
        f"{index + 1}. {option}" for index, option in enumerate(options)
    )
    await event.answer(
        text,
        reply_markup=campaign._plot_keyboard(options, False),
    )


def _player_name(session, user_id: int, fallback: str | None = None) -> str:
    participant = (getattr(session, "participants", {}) or {}).get(str(int(user_id)), {}) if session else {}
    return participant.get("name") or fallback or "Егрок"


def _format_profile(profile: dict | None) -> list[str]:
    profile = profile or {}
    lines = []
    for key, label in _PROFILE_LABELS.items():
        if profile.get(key):
            lines.append(f"{label}: {profile[key]}")
    return lines


def _format_reputation(values) -> str | None:
    reputation = [str(value).strip() for value in (values or []) if str(value).strip()]
    if not reputation:
        return None
    return "; ".join(reputation[-6:])


def render_hero(dnd, chat_id: int, user_id: int, user_name: str | None = None) -> str:
    campaign = _campaign_module(dnd)
    session = _active_session(dnd, chat_id)
    key = str(int(user_id))

    if session is not None and key in (getattr(session, "participants", {}) or {}):
        profile = (getattr(session, "character_profiles", {}) or {}).get(key) or {}
        reputation = (getattr(session, "reputations", {}) or {}).get(key) or []
        items = (getattr(session, "inventories", {}) or {}).get(key) or []
        name = _player_name(session, user_id, user_name)
        lines = [f"🎭 Мой герой — {name}"]
        profile_lines = _format_profile(profile)
        if profile_lines:
            lines.extend(profile_lines)
        else:
            lines.append("Профиль ещё не выбран.")
        rep_text = _format_reputation(reputation)
        if rep_text:
            lines.append(f"🏷 Репутация: {rep_text}")
        lines.append("🎒 Инвентарь")
        lines.extend(_inventory_items(items) or ["Пусто."])
        return "\n".join(lines)

    history = campaign._player_history(chat_id, user_id)
    if not history:
        return "🎭 Героя пока нет: ты ещё не сохранился ни в одной завершённой егре этого чата."

    lines = [f"🎭 Мой герой — {history.get('name') or user_name or 'Егрок'}"]
    profile_lines = _format_profile(history.get("profile"))
    if profile_lines:
        lines.extend(profile_lines)
    else:
        lines.append("Профиль не сохранился.")
    rep_text = _format_reputation(history.get("reputation"))
    if rep_text:
        lines.append(f"🏷 Репутация: {rep_text}")
    adventures = history.get("adventures") or []
    if adventures:
        lines.append(f"📚 Завершённых приключений в памяти: {len(adventures)}")
    lines.append("🎒 Инвентарь")
    lines.extend(_inventory_items(history.get("inventory")) or ["Пусто."])
    return "\n".join(lines)


def _inventory_items(items) -> list[str]:
    result = []
    for item in items or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            kind = str(item.get("kind") or "item").casefold()
        else:
            name = str(item).strip()
            kind = "item"
        if name:
            result.append(("✨ " if kind == "artifact" else "• ") + name)
    return result


def render_inventory(dnd, chat_id: int, user_id: int) -> str:
    campaign = _campaign_module(dnd)
    session = _active_session(dnd, chat_id)
    key = str(int(user_id))

    if session is not None and key in (getattr(session, "participants", {}) or {}):
        items = (getattr(session, "inventories", {}) or {}).get(key) or []
        lines = ["🎒 Инвентарь", "Источник: текущая егра."]
    else:
        history = campaign._player_history(chat_id, user_id)
        if not history:
            return "🎒 Инвентарь пуст: сохранённых вещей у тебя в этом чате нет."
        items = history.get("inventory") or []
        lines = ["🎒 Инвентарь", "Источник: последнее сохранённое состояние."]

    formatted = _inventory_items(items)
    lines.extend(formatted or ["Пусто. Даже фантика не нажили."])
    return "\n".join(lines)


def _format_npcs(npc_memory: dict | None, *, limit: int = 12) -> list[str]:
    values = list((npc_memory or {}).values())
    lines = []
    for item in values[-limit:]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Неизвестный тип").strip()
        details = []
        event = str(item.get("event") or "").strip()
        if event:
            details.append(event)
        notes = [str(note).strip() for note in (item.get("notes") or []) if str(note).strip()]
        if notes:
            details.append(" / ".join(notes[-2:]))
        lines.append(f"• {name}" + (f" — {'; '.join(details)}" if details else ""))
    if len(values) > limit:
        lines.append(f"…и ещё {len(values) - limit} персонажей в памяти.")
    return lines


async def _repair_active_npc_memory(dnd, chat_id: int) -> None:
    session = _active_session(dnd, chat_id)
    if session is None:
        return
    campaign = _campaign_module(dnd)

    # Recover only historical NPC tags. Replaying all metadata would apply old
    # THREAT deltas a second time and could corrupt the live campaign state.
    before = len(getattr(session, "npc_memory", {}) or {})
    for item in getattr(session, "conversation", []) or []:
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        npc_tags = _NPC_TAG_RE.findall(str(item.get("content") or ""))
        if npc_tags:
            campaign._apply_metadata(session, "\n".join(npc_tags))
    if len(getattr(session, "npc_memory", {}) or {}) > before:
        dnd.persist_dnd_sessions()

    if getattr(session, "npc_memory", {}) or {}:
        return
    scenes = [str(scene).strip() for scene in (getattr(session, "scene_log", []) or [])[-12:] if str(scene).strip()]
    if not scenes:
        return

    participant_names = [
        str(item.get("name") or "").strip()
        for item in (getattr(session, "participants", {}) or {}).values()
        if str(item.get("name") or "").strip()
    ]
    prompt = (
        "Служебное восстановление связей D&D. Проанализируй ТОЛЬКО реальные сцены ниже и найди именованных NPC, "
        "с которыми партия уже взаимодействовала. Игроков не считай NPC. Не выдумывай новых персонажей. "
        "Для каждого найденного NPC верни отдельную строку строго в формате "
        "[NPC:Имя;EVENT:кратко что связывает с партией;NOTE:последний важный факт]. "
        "Если именованных NPC нет, ответь только NONE. Никаких ACTION-тегов и пояснений.\n"
        f"ИГРОКИ: {', '.join(participant_names) or 'не указаны'}\n"
        "СЦЕНЫ:\n" + "\n---\n".join(scenes)
    )
    try:
        raw = await campaign._ephemeral_generate(dnd, session, prompt)
        campaign._apply_metadata(session, raw)
        dnd.persist_dnd_sessions()
    except Exception:
        logging.exception("DnD NPC memory repair failed chat_id=%s", chat_id)


def render_npcs(dnd, chat_id: int) -> str:
    campaign = _campaign_module(dnd)
    session = _active_session(dnd, chat_id)
    if session is not None:
        memory = getattr(session, "npc_memory", {}) or {}
        lines = ["🤝 Связи", "Источник: текущая егра."]
    else:
        latest = campaign._latest_campaign(chat_id)
        if not latest:
            return "🤝 Связей пока нет: завершённых кампаний в этом чате не найдено."
        memory = latest.get("npc_memory") or {}
        lines = ["🤝 Связи", "Источник: последняя завершённая егра."]

    formatted = _format_npcs(memory)
    lines.extend(formatted or ["Пока ни одного сюжетного NPC не запомнили."])
    return "\n".join(lines)


def _threat_line(threat: dict | None) -> str | None:
    threat = threat or {}
    name = str(threat.get("name") or "").strip()
    if not name:
        return None
    try:
        level = int(threat.get("level", 0))
        maximum = max(1, int(threat.get("max", 6)))
    except (TypeError, ValueError):
        return f"⚠️ Угроза: {name}"
    level = max(0, min(maximum, level))
    bar = "■" * level + "□" * (maximum - level)
    return f"⚠️ Угроза: {name} — {bar} {level}/{maximum}"


def _active_detail(dnd, session) -> list[str]:
    state = getattr(session, "state", None)
    lines = [f"Сейчас: {_STATE_LABELS.get(state, str(state or 'егра идёт'))}."]
    if state == "WAITING_ROLL":
        roll = getattr(session, "pending_roll", None) or {}
        reason = roll.get("reason")
        if reason:
            detail = f"🎲 Бросок: {reason}"
            if roll.get("dc") is not None:
                detail += f", сложность {roll['dc']}"
            mode = _MODE_LABELS.get(str(roll.get("mode") or "NORMAL").upper())
            if mode and mode != "обычный":
                detail += f", {mode}"
            lines.append(detail + ".")
    elif state == "WAITING_POLL":
        poll = getattr(session, "pending_poll", None) or {}
        options = [str(value) for value in (poll.get("options") or []) if str(value).strip()]
        if options:
            lines.append("🗳 Выбор: " + " / ".join(options[:4]))
        votes = poll.get("votes") or {}
        lines.append(f"Голосов уже: {len(votes)}.")
    elif state == "WAITING_ACTION":
        pending = getattr(session, "pending_actions", {}) or {}
        lines.append(f"🎭 Действий уже заявлено: {len(pending)}.")
        targets = list(getattr(session, "action_target_user_ids", []) or [])
        if targets:
            try:
                names = dnd._target_names(session, targets)
            except Exception:
                names = [str(value) for value in targets]
            if names:
                lines.append("Ход адресован: " + ", ".join(names) + ".")
    elif state == "LOBBY":
        participants = list((getattr(session, "participants", {}) or {}).values())
        if participants:
            lines.append("Уже влезли: " + ", ".join(str(item.get("name") or "Егрок") for item in participants) + ".")
    elif state == "WAITING_PLOT":
        options = getattr(session, "plot_options", []) or []
        if options:
            lines.append(f"На выбор подготовлено сюжетов: {len(options)}.")
    return lines


def render_status(dnd, chat_id: int) -> str:
    campaign = _campaign_module(dnd)
    session = _active_session(dnd, chat_id)
    if session is not None:
        blocks = ["🧭 Что происходит?", "Егра сейчас активна."]
        plot = getattr(session, "selected_plot", None)
        if plot:
            blocks.append(f"🎬 Сюжет\n{plot}")
        detail = _active_detail(dnd, session)
        if detail:
            blocks.append("\n".join(detail))
        scene_log = getattr(session, "scene_log", []) or []
        if scene_log:
            blocks.append(f"📍 Последняя сцена\n{scene_log[-1]}")
        threat = _threat_line(getattr(session, "threat", None))
        if threat:
            blocks.append(threat)
        return "\n\n".join(blocks)

    latest = campaign._latest_campaign(chat_id)
    if not latest:
        return "🧭 Сейчас ничего не происходит: активной егры нет и сохранённых кампаний тоже нет."

    blocks = [
        "🧭 Что происходит?",
        "Активной егры сейчас нет.\nПоказываю последнюю завершённую.",
    ]
    if latest.get("selected_plot"):
        blocks.append(f"🎬 Сюжет\n{latest['selected_plot']}")
    finale = str(latest.get("finale") or "").strip()
    epilogue = str(latest.get("epilogue") or "").strip()
    scenes = latest.get("scenes") or []
    if finale:
        blocks.append(f"🏁 Финал\n{finale}")
    elif scenes:
        blocks.append(f"📍 Последняя сцена\n{scenes[-1]}")
    if epilogue:
        blocks.append(f"📚 Эпилог\n{epilogue}")
    threat = _threat_line(latest.get("threat"))
    if threat:
        blocks.append(threat)
    return "\n\n".join(blocks)


def render_state_command(kind: str, dnd, chat_id: int, user_id: int, user_name: str | None = None) -> str:
    if kind == "hero":
        return render_hero(dnd, chat_id, user_id, user_name)
    if kind == "inventory":
        return render_inventory(dnd, chat_id, user_id)
    if kind == "npcs":
        return render_npcs(dnd, chat_id)
    if kind == "status":
        return render_status(dnd, chat_id)
    raise ValueError(f"Unknown DnD state command: {kind}")


class DndStateCommandMiddleware(BaseMiddleware):
    """Intercept state queries before DnD's action collector can treat them as moves."""

    async def __call__(self, handler, event, data):
        kind = command_kind(getattr(event, "text", None))
        if kind is None:
            return await handler(event, data)

        from AI import dnd

        if kind == "start":
            await dnd.cmd_start_dnd(event)
            return None

        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is None or user is None or not hasattr(event, "answer"):
            return await handler(event, data)

        if kind == "end":
            await _repair_active_npc_memory(dnd, int(chat.id))
            await dnd.cmd_stop_dnd(event)
            return None
        if kind == "legacy_end":
            await event.answer("Команда завершения теперь — «днд конец».")
            return None
        if kind == "lobby_menu":
            await _repost_lobby(event, dnd)
            return None
        if kind == "lobby_start":
            await _start_lobby_from_message(event, dnd)
            return None
        if kind == "npcs":
            await _repair_active_npc_memory(dnd, int(chat.id))

        text = render_state_command(
            kind,
            dnd,
            int(chat.id),
            int(user.id),
            getattr(user, "first_name", None),
        )
        await event.answer(text)
        return None


def configure_dnd_state_commands(dnd_router) -> None:
    """Register state access before DnD's action collector can treat them as moves."""
    if getattr(dnd_router, "_upupa_dnd_state_commands_configured", False):
        return
    dnd_router.message.outer_middleware(DndStateCommandMiddleware())
    dnd_router._upupa_dnd_state_commands_configured = True
