"""Read-only history of completed DnD campaigns."""
from __future__ import annotations

from datetime import datetime
import re

from aiogram import BaseMiddleware


_PARTY_HISTORY_ALIASES = {"днд партии"}
_TECH_TAG_RE = re.compile(r"\[(?:ACTION|THREAT|NPC|ITEM|REP):[^\]]*\]", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_MAX_VISIBLE_PARTIES = 20
_PLOT_LIMIT = 56
_DETAIL_LIMIT = 58

_OUTCOME_RULES = (
    ("dead", "☠️", "погиб", re.compile(
        r"\b(?:сдох\w*|погиб\w*|умер\w*|убит\w*|мертв\w*|мёртв\w*|захлеб\w*|утонул\w*|"
        r"раздав\w*|завалил\w*|прикончил\w*|тушк\w*|дыр\w*.{0,12}башк\w*)", re.I)),
    ("captured", "⛓", "в плену", re.compile(
        r"\b(?:в плен\w*|пленен\w*|пленён\w*|схвачен\w*|заперт\w*|в клетк\w*|забрали\w*)", re.I)),
    ("injured", "🩹", "ранен", re.compile(
        r"\b(?:ранен\w*|покалеч\w*|пробит\w*|пролом\w*|сломал\w*|без сознания|лишил\w*)", re.I)),
    ("alive", "✅", "выжил", re.compile(
        r"\b(?:выжил\w*|уцелел\w*|спасся\w*|выбрался\w*|остал\w* жив\w*)", re.I)),
)
_GROUP_ALIVE_RE = re.compile(r"\b(?:все\s+выжил\w*|все\s+остал\w*\s+жив\w*|вы\s+остал\w*\s+жив\w*|никто\s+не\s+погиб\w*)", re.I)
_GROUP_DEAD_RE = re.compile(r"\b(?:все\s+(?:сдох\w*|погиб\w*|умер\w*)|никто\s+не\s+выжил\w*)", re.I)
_STATUS_PREFIX_RE = re.compile(
    r"^(?:сдох\w*|погиб\w*|умер\w*|убит\w*|мертв\w*|мёртв\w*|захлеб\w*|утонул\w*|"
    r"в плен\w*|пленен\w*|пленён\w*|схвачен\w*|заперт\w*|забрали\w*|"
    r"ранен\w*|покалеч\w*|выжил\w*|уцелел\w*|спасся\w*|выбрался\w*|остал\w* жив\w*)"
    r"[\s,:;—-]*",
    re.I,
)


def _normalize_command(text: str | None) -> str:
    value = " ".join(str(text or "").strip().casefold().split())
    return value.rstrip(" ?!.,:;").strip()


def is_party_history_command(text: str | None) -> bool:
    return _normalize_command(text) in _PARTY_HISTORY_ALIASES


def _campaign_module(dnd):
    from AI import dnd_campaign

    dnd_campaign._load_archive(dnd)
    return dnd_campaign


def _clean_text(value) -> str:
    text = _TECH_TAG_RE.sub("", str(value or ""))
    return " ".join(text.split()).strip()


def _compact_text(value, limit: int) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip(" ,.;:—-") + "…"


def _completed_date(row: dict) -> str:
    raw = str(row.get("completed_at") or "").strip()
    if not raw:
        return "дата неизвестна"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw.split("T", 1)[0] or "дата неизвестна"
    return parsed.strftime("%d.%m.%Y")


def _summary_sources(row: dict) -> list[str]:
    return [
        text
        for text in (
            _clean_text(row.get("epilogue")),
            _clean_text(row.get("finale")),
            _clean_text((row.get("scenes") or [None])[-1]),
        )
        if text
    ]


def _player_names(row: dict, chat: dict) -> list[tuple[str, str]]:
    snapshot = row.get("participants") or {}
    profiles = row.get("profiles") or {}
    inventories = row.get("inventories") or {}
    keys = list(dict.fromkeys([*snapshot.keys(), *profiles.keys(), *inventories.keys()]))
    historical_players = chat.get("players") or {}
    result = []
    for key in keys:
        player = historical_players.get(str(key)) or {}
        name = snapshot.get(str(key)) or player.get("name")
        if isinstance(name, dict):
            name = name.get("name")
        name = _clean_text(name)
        if name:
            result.append((str(key), name))
    return result


def _sentence_with_name(source: str, name: str) -> str:
    needle = name.casefold()
    chunks = [chunk.strip() for chunk in _SENTENCE_SPLIT_RE.split(source) if chunk.strip()]
    matches = [chunk for chunk in chunks if needle in chunk.casefold()]
    if not matches and needle in source.casefold():
        matches = [source]
    return matches[-1] if matches else ""


def _classify_outcome(text: str) -> tuple[str, str, str] | None:
    for key, icon, label, pattern in _OUTCOME_RULES:
        if pattern.search(text):
            return key, icon, label
    return None


def _outcome_detail(sentence: str, name: str) -> str:
    lowered = sentence.casefold()
    index = lowered.find(name.casefold())
    fragment = sentence[index + len(name):] if index >= 0 else sentence
    fragment = fragment.lstrip(" ,:;—-–")
    fragment = _STATUS_PREFIX_RE.sub("", fragment, count=1)
    return _compact_text(fragment, _DETAIL_LIMIT)


def _group_outcome(sources: list[str]) -> str | None:
    combined = " ".join(sources)
    if _GROUP_DEAD_RE.search(combined):
        return "dead"
    if _GROUP_ALIVE_RE.search(combined):
        return "alive"
    return None


def _player_outcomes(row: dict, chat: dict) -> list[dict[str, str]]:
    sources = _summary_sources(row)
    group_key = _group_outcome(sources)
    outcomes = []
    for _player_id, name in _player_names(row, chat):
        sentence = ""
        for source in sources:
            sentence = _sentence_with_name(source, name)
            if sentence:
                break
        classified = _classify_outcome(sentence) if sentence else None
        detail = _outcome_detail(sentence, name) if sentence else ""
        if classified:
            key, icon, label = classified
        elif group_key == "alive":
            key, icon, label = "alive", "✅", "выжил"
        elif group_key == "dead":
            key, icon, label = "dead", "☠️", "погиб"
        else:
            key, icon, label = "unknown", "•", ""
        outcomes.append({"name": name, "key": key, "icon": icon, "label": label, "detail": detail})
    return outcomes


def _format_outcome(row: dict, outcomes: list[dict[str, str]]) -> str:
    if outcomes:
        keys = [item["key"] for item in outcomes]
        if all(key == "alive" for key in keys):
            return "✅ Все выжили."
        if all(key == "dead" for key in keys):
            return "☠️ Никто не выжил."

        bits = []
        for item in outcomes:
            if item["key"] == "unknown" and not item["detail"]:
                continue
            status = f"{item['icon']} {item['name']}"
            if item["label"]:
                status += f" — {item['label']}"
            if item["detail"]:
                status += f": {item['detail']}"
            bits.append(status)
        if bits:
            return "; ".join(bits)

    sources = _summary_sources(row)
    return _compact_text(sources[0], 120) if sources else "Итог не сохранился."


def render_party_history(dnd, chat_id: int) -> str:
    campaign = _campaign_module(dnd)
    chat = campaign._chat_history(chat_id)
    rows = list((chat.get("campaigns") or []))
    if not rows:
        return "🎲 Прошедших партий в сохранённом архиве этого чата пока нет."

    visible = rows[-_MAX_VISIBLE_PARTIES:][::-1]
    lines = [f"🎲 Партии DnD · {len(rows)}", ""]
    for index, row in enumerate(visible, start=1):
        plot = _compact_text(row.get("selected_plot"), _PLOT_LIMIT) or "Безымянная катастрофа"
        outcomes = _player_outcomes(row, chat)
        lines.append(f"{index}. {_completed_date(row)} · {plot}")
        if outcomes:
            lines.append("   👥 " + ", ".join(item["name"] for item in outcomes))
        lines.append("   🏁 " + _format_outcome(row, outcomes))
        lines.append("")

    hidden = len(rows) - len(visible)
    if hidden > 0:
        lines.append(f"…ещё {hidden} более старых записей не показано.")
    return "\n".join(lines).rstrip()


class DndPartyHistoryMiddleware(BaseMiddleware):
    """Intercept ``днд партии`` before the active-game action collector sees it."""

    async def __call__(self, handler, event, data):
        if not is_party_history_command(getattr(event, "text", None)):
            return await handler(event, data)

        chat = getattr(event, "chat", None)
        if chat is None or not hasattr(event, "answer"):
            return await handler(event, data)

        from AI import dnd

        await event.answer(render_party_history(dnd, int(chat.id)))
        return None


def install_dnd_party_history(dnd_router) -> None:
    """Install the history command before participant-action completion middleware."""
    if getattr(dnd_router, "_upupa_dnd_party_history_configured", False):
        return
    middleware = DndPartyHistoryMiddleware()
    dnd_router.message.outer_middleware(middleware)
    dnd_router._upupa_dnd_party_history_middleware = middleware
    dnd_router._upupa_dnd_party_history_configured = True
