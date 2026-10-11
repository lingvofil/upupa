"""«Светская хроника» — a weekly gossip column built from social-graph facts.

Facts are deterministic (pair of the week, who grew closer, who cooled down,
new pairs, the most sociable person, Chronicle episodes).  AI only turns them
into a few lively sentences; without AI the same facts are posted as a list.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
import logging
from zoneinfo import ZoneInfo

from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings import APP_TIMEZONE_NAME
from core.state import chat_settings
from features.chronicle.models import ChronicleEvent
from features.chronicle.runtime import recent_events
from features.social_graph.dynamics import _plural
from features.social_graph.service import (
    _without_username_tag,
    is_social_graph_enabled,
    list_active_chats,
    load_chat_daily,
)
from infrastructure.ai.execution import ai_feature


DIGEST_WEEKDAY = 6  # Sunday
DIGEST_HOUR = 19
DIGEST_CHECK_SECONDS = 1800
PAIR_OF_WEEK_MIN_EVENTS = 5
RISING_MIN_EVENTS = 4
COOLING_MIN_PREVIOUS_AVG = 3.0
NEW_PAIR_MIN_EVENTS = 3
MAX_EVENTS = 3
SETTINGS_KEY = "social_digest_enabled"
SENT_WEEK_KEY = "social_digest_week"
_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


@dataclass(frozen=True, slots=True)
class PairWeek:
    user_a_id: int
    user_b_id: int
    user_a_name: str
    user_b_name: str
    this_week: int
    previous_avg: float
    is_new: bool = False

    @property
    def title(self) -> str:
        return f"{self.user_a_name} и {self.user_b_name}"


@dataclass(frozen=True, slots=True)
class DigestFacts:
    chat_id: int
    week_start: date
    week_end: date
    total_interactions: int
    pair_of_week: PairWeek | None
    rising: PairWeek | None
    cooling: PairWeek | None
    new_pairs: tuple[PairWeek, ...]
    person_of_week: tuple[int, str, int] | None
    events: tuple[ChronicleEvent, ...]

    @property
    def is_meaningful(self) -> bool:
        return self.pair_of_week is not None or bool(self.events)

    @property
    def week_label(self) -> str:
        if self.week_start.month == self.week_end.month:
            return f"{self.week_start.day}–{self.week_end.day} {_MONTHS[self.week_end.month - 1]}"
        return (
            f"{self.week_start.day} {_MONTHS[self.week_start.month - 1]} – "
            f"{self.week_end.day} {_MONTHS[self.week_end.month - 1]}"
        )


def is_digest_enabled(chat_id: int | str) -> bool:
    # Opt-in: a weekly autopost must be switched on explicitly by the chat.
    return bool(chat_settings.get(str(chat_id), {}).get(SETTINGS_KEY, False))


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def compute_digest_facts(
    chat_id: int,
    rows: list[dict],
    events: list[ChronicleEvent],
    *,
    today: date,
) -> DigestFacts:
    week_start = today - timedelta(days=6)
    previous_start = week_start - timedelta(days=21)

    pairs: dict[tuple[int, int], dict] = {}
    partners: dict[int, set[int]] = defaultdict(set)
    person_events: dict[int, int] = defaultdict(int)
    person_names: dict[int, str] = {}
    total = 0
    for row in rows:
        day = _day(row.get("activity_date"))
        if day is None or day < previous_start or day > today:
            continue
        a, b = int(row["user_a_id"]), int(row["user_b_id"])
        count = max(0, int(row.get("event_count") or 0))
        name_a = _without_username_tag(str(row.get("user_a_name") or "Участник"))
        name_b = _without_username_tag(str(row.get("user_b_name") or "Участник"))
        bucket = pairs.setdefault((a, b), {
            "names": (name_a, name_b), "week": 0, "previous": 0, "first": _day(row.get("first_interaction_at")),
        })
        if day >= week_start:
            bucket["week"] += count
            total += count
            partners[a].add(b)
            partners[b].add(a)
            person_events[a] += count
            person_events[b] += count
            person_names[a], person_names[b] = name_a, name_b
        else:
            bucket["previous"] += count

    weeks = [
        PairWeek(
            user_a_id=a,
            user_b_id=b,
            user_a_name=data["names"][0],
            user_b_name=data["names"][1],
            this_week=data["week"],
            previous_avg=data["previous"] / 3.0,
            is_new=data["first"] is not None and data["first"] >= week_start,
        )
        for (a, b), data in pairs.items()
    ]

    active = [item for item in weeks if item.this_week >= PAIR_OF_WEEK_MIN_EVENTS]
    pair_of_week = max(active, key=lambda item: (item.this_week, -item.user_a_id), default=None)
    used = {(pair_of_week.user_a_id, pair_of_week.user_b_id)} if pair_of_week else set()

    rising_pool = [
        item for item in weeks
        if (item.user_a_id, item.user_b_id) not in used
        and not item.is_new
        and item.this_week >= RISING_MIN_EVENTS
        and item.this_week >= 2.0 * max(item.previous_avg, 1.0)
    ]
    rising = max(rising_pool, key=lambda item: (item.this_week - item.previous_avg, -item.user_a_id), default=None)
    if rising is not None:
        used.add((rising.user_a_id, rising.user_b_id))

    cooling_pool = [
        item for item in weeks
        if (item.user_a_id, item.user_b_id) not in used
        and item.previous_avg >= COOLING_MIN_PREVIOUS_AVG
        and item.this_week <= item.previous_avg * 0.3
    ]
    cooling = max(cooling_pool, key=lambda item: (item.previous_avg - item.this_week, -item.user_a_id), default=None)

    new_pairs = tuple(sorted(
        (item for item in weeks if item.is_new and item.this_week >= NEW_PAIR_MIN_EVENTS and (item.user_a_id, item.user_b_id) not in used),
        key=lambda item: (-item.this_week, item.user_a_id),
    )[:2])

    person = None
    if partners:
        user_id = max(partners, key=lambda uid: (len(partners[uid]), person_events[uid], -uid))
        if len(partners[user_id]) >= 3:
            person = (user_id, person_names.get(user_id, "Участник"), len(partners[user_id]))

    week_events = tuple(sorted(
        (event for event in events if week_start <= event.event_started_at.date() <= today),
        key=lambda event: (-event.importance_score, event.event_started_at),
    )[:MAX_EVENTS])

    return DigestFacts(
        chat_id=int(chat_id),
        week_start=week_start,
        week_end=today,
        total_interactions=total,
        pair_of_week=pair_of_week,
        rising=rising,
        cooling=cooling,
        new_pairs=new_pairs,
        person_of_week=person,
        events=week_events,
    )


async def build_digest_facts(chat_id: int, *, today: date | None = None) -> DigestFacts:
    today = today or datetime.now(ZoneInfo(APP_TIMEZONE_NAME)).date()
    since = (today - timedelta(days=27)).isoformat()
    rows = await load_chat_daily(chat_id, since)
    try:
        events = await recent_events(chat_id, days=8, limit=50)
    except Exception as exc:
        logging.warning("[social_digest] chronicle unavailable chat=%s: %s", chat_id, exc)
        events = []
    return compute_digest_facts(chat_id, rows, list(events), today=today)


def _times(count: int) -> str:
    return f"{count} {_plural(count, 'взаимодействие', 'взаимодействия', 'взаимодействий')}"


def fact_lines(facts: DigestFacts) -> list[str]:
    """Plain-text facts: the AI input and the no-AI fallback body."""
    lines = []
    if facts.pair_of_week:
        lines.append(f"Пара недели: {facts.pair_of_week.title} — {_times(facts.pair_of_week.this_week)} за неделю.")
    if facts.rising:
        lines.append(
            f"Резко сблизились: {facts.rising.title} — {facts.rising.this_week} за неделю "
            f"против обычных {facts.rising.previous_avg:.0f}."
        )
    if facts.cooling:
        lines.append(
            f"Остыли: {facts.cooling.title} — {facts.cooling.this_week} за неделю "
            f"против обычных {facts.cooling.previous_avg:.0f}."
        )
    for pair in facts.new_pairs:
        lines.append(f"Новая пара: {pair.title} впервые разговорились — {_times(pair.this_week)}.")
    if facts.person_of_week:
        _uid, name, partners = facts.person_of_week
        lines.append(f"Светский лев недели: {name} — {partners} {_plural(partners, 'собеседник', 'собеседника', 'собеседников')} за неделю.")
    for event in facts.events:
        lines.append(f"Эпизод Летописи: «{event.title}» — {event.summary}")
    return lines


def _message_link(chat_id: int, message_id: int | None) -> str | None:
    raw = str(int(chat_id))
    if message_id is None or not raw.startswith("-100"):
        return None
    return f"https://t.me/c/{raw[4:]}/{int(message_id)}"


def digest_keyboard(chat_id: int | None = None):
    from features.social_graph.webapp import mini_app_link

    builder = InlineKeyboardBuilder()
    link = mini_app_link(chat_id) if chat_id is not None else None
    if link:
        builder.button(text="🌐 Интерактивная карта", url=link)
    builder.button(text="🕸 Карта", callback_data="sg:map")
    builder.button(text="🏴 Фракции", callback_data="sg:fac")
    builder.button(text="📜 Летопись", callback_data="sg:chr")
    builder.adjust(*((1,) if link else ()), 3)
    return builder.as_markup()


async def _default_generator(prompt: str, chat_id: str) -> str:
    from AI.dialog.generation import generate_simple_response

    return await generate_simple_response(prompt, chat_id)


@ai_feature("светская хроника")
async def write_digest_html(facts: DigestFacts, *, generator=None) -> str:
    facts_text = fact_lines(facts)
    body = None
    if facts_text:
        prompt = (
            "Ты ведёшь колонку «Светская хроника» в Telegram-чате: еженедельная сплетня о том, кто с кем общался. "
            "Напиши 4–7 живых саркастичных предложений одним абзацем, как светский обозреватель. "
            "Используй ТОЛЬКО факты ниже: не добавляй событий, причин, чувств, романов, цитат и чисел, которых нет. "
            "Можно иронизировать над формулировками фактов. Без markdown, без списков, без хэштегов.\n"
            "Факты недели:\n- " + "\n- ".join(facts_text)
        )
        try:
            raw = await (generator or _default_generator)(prompt, str(facts.chat_id))
            compact = " ".join(str(raw or "").split()).strip()
            if compact and not compact.lower().startswith(("ошибка", "произошла ошибка", "я пока не знаю")):
                body = escape(compact[:1500])
        except Exception as exc:
            logging.warning("[social_digest] AI column failed chat=%s: %s", facts.chat_id, exc)

    lines = [f"🥂 <b>Светская хроника</b> · {facts.week_label}", ""]
    if body:
        lines.append(body)
    else:
        lines.extend(f"• {escape(line)}" for line in fact_lines(facts) if not line.startswith("Эпизод Летописи"))
    if facts.events:
        lines.extend(("", "<b>Эпизоды недели</b>"))
        for event in facts.events:
            title = escape(event.title)
            link = _message_link(facts.chat_id, event.anchor_message_id or (event.source_message_ids[0] if event.source_message_ids else None))
            lines.append(f'• <a href="{escape(link, quote=True)}">{title}</a>' if link else f"• {title}")
    return "\n".join(lines)


def week_key(moment: datetime) -> str:
    year, week, _weekday = moment.isocalendar()
    return f"{year}-W{week:02d}"


async def run_due_digests(bot, *, now: datetime | None = None) -> int:
    """Post the column once per ISO week, on Sunday evening local time."""
    from features.chat_settings import save_chat_settings

    now = now or datetime.now(ZoneInfo(APP_TIMEZONE_NAME))
    if now.weekday() != DIGEST_WEEKDAY or now.hour < DIGEST_HOUR:
        return 0
    key = week_key(now)
    sent = 0
    for chat_id in await list_active_chats(days=7):
        cid = str(chat_id)
        if not is_social_graph_enabled(chat_id) or not is_digest_enabled(chat_id):
            continue
        if chat_settings.get(cid, {}).get(SENT_WEEK_KEY) == key:
            continue
        # Mark before sending so an unreachable chat is not retried every 30 minutes.
        chat_settings.setdefault(cid, {})[SENT_WEEK_KEY] = key
        save_chat_settings()
        try:
            facts = await build_digest_facts(chat_id, today=now.date())
            if not facts.is_meaningful:
                continue
            text = await write_digest_html(facts)
            await bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=digest_keyboard(chat_id),
            )
            sent += 1
        except Exception as exc:
            logging.warning("[social_digest] chat=%s failed: %s", chat_id, exc)
        await asyncio.sleep(3)
    return sent


async def social_digest_loop(bot) -> None:
    await asyncio.sleep(180)
    while True:
        try:
            sent = await run_due_digests(bot)
            if sent:
                logging.info("[social_digest] posted to %s chats", sent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.error("[social_digest] loop error: %s", exc, exc_info=True)
        await asyncio.sleep(DIGEST_CHECK_SECONDS)
