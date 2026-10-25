"""Personal facts for the dossier, gathered from other Upupa features.

The social graph knows who talks to whom; everything else about a person —
activity, daily rhythm, reactions, games, birthday, how they use Upupa and the
favourite word — lives in other features.  Every source is optional and fails
soft: the dossier must render even when statistics or the DnD archive are
unavailable.  The phrase helpers at the bottom keep wording identical between
the card, its caption and the detailed text page.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
import logging
import time
from typing import Sequence

from core.time_utils import app_now_naive
from features.social_graph.ai import invent_nickname
from features.social_graph.dynamics import _plural
from infrastructure.ai.execution import ai_feature_context


RHYTHM_PERIOD_DAYS = 90
UPUPA_PERIOD_DAYS = 90
RHYTHM_MIN_MESSAGES = 20
VERBOSITY_MIN_MESSAGES = 20
# Average words per message → manner of writing; the last matching bound wins.
VERBOSITY_STYLES = ((0, "односложно"), (3, "коротко"), (7, "развёрнуто"), (15, "простынями"))
PEAK_WEEKDAY_MIN_SHARE = 0.2
SILENCE_MIN_DAYS = 3
TOP_ITEMS = 3
FACTS_TTL_SECONDS = 5 * 60
FACTS_CACHE_SIZE = 128
NICKNAME_TTL_SECONDS = 24 * 3600
NICKNAME_FAILURE_TTL_SECONDS = 15 * 60
NICKNAME_TIMEOUT_SECONDS = 20.0

# AI calls attributed to a user without the user asking Upupa for anything.
PASSIVE_AI_FEATURES = frozenset({
    "случайные AI-реакции", "проактивный вброс", "летопись", "досье", "итоги года",
})
_FEATURE_LABELS = {"диалог": "поболтать", "DnD": "ДнД"}

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
WEEKDAYS_DATIVE = ("понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям")
CHRONOTYPES = {
    "owl": ("🦉", "сова"),
    "lark": ("🐦", "жаворонок"),
    "day": ("☀️", "дневной"),
    "evening": ("🌆", "вечерний"),
}
# The card font has no emoji, so the picture names reactions in words.  Forms are
# accusative: they always follow «ставит …» / «получает …».
REACTION_WORDS = {
    "👍": "лайк", "👎": "дизлайк", "❤": "сердечко", "🔥": "огонь", "🥰": "умиление", "👏": "аплодисменты",
    "😁": "лыбу", "🤔": "задумчивость", "🤯": "взрыв мозга", "😱": "ужас", "🤬": "мат", "😢": "слёзы",
    "🎉": "праздник", "🤩": "восторг", "🤮": "рвоту", "💩": "какашку", "🙏": "мольбу", "👌": "окей",
    "🕊": "голубя", "🤡": "клоуна", "🥱": "зевок", "🥴": "угар", "😍": "влюблённость", "🐳": "кита",
    "❤‍🔥": "горящее сердце", "🌚": "луну", "🌭": "хот-дог", "💯": "сотку", "🤣": "ржач", "⚡": "молнию",
    "🍌": "банан", "🏆": "кубок", "💔": "разбитое сердце", "🤨": "подозрение", "😐": "покерфейс",
    "🍓": "клубничку", "🍾": "шампанское", "💋": "поцелуй", "🖕": "фак", "😈": "чертёнка", "😴": "сон",
    "😭": "рыдание", "🤓": "ботана", "👻": "призрака", "👀": "глаза", "🎃": "тыкву", "🙈": "стыд",
    "😇": "ангела", "😨": "испуг", "🤝": "рукопожатие", "✍": "конспект", "🤗": "обнимашки", "🫡": "честь",
    "🎅": "Санту", "🎄": "ёлку", "☃": "снеговика", "💅": "маникюр", "🤪": "придурь", "🗿": "моаи",
    "🆒": "кул", "💘": "купидона", "🙉": "«не слышу»", "🦄": "единорога", "😘": "воздушный поцелуй",
    "💊": "таблетку", "🙊": "молчок", "😎": "крутые очки", "👾": "пришельца", "🤷": "пофиг", "😡": "злость",
}


@dataclass(frozen=True, slots=True)
class ActivityFacts:
    total: int = 0
    weekly: int = 0
    rank: str | None = None
    last_seen: datetime | None = None


@dataclass(frozen=True, slots=True)
class RhythmFacts:
    hours: tuple[int, ...]
    chronotype: str
    peak_hour: int
    peak_weekday: int | None = None  # 0 = понедельник


@dataclass(frozen=True, slots=True)
class ReactionFacts:
    given: tuple[tuple[str, int], ...] = ()
    received: tuple[tuple[str, int], ...] = ()
    best_message_id: int | None = None
    best_message_reactions: int = 0
    best_message_text: str = ""


@dataclass(frozen=True, slots=True)
class CrocodileFacts:
    draws: int = 0
    likes: int = 0
    average_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class DndFacts:
    adventures: int
    hero: str | None = None
    archetype: str | None = None
    dead: bool = False
    last_plot: str | None = None


@dataclass(frozen=True, slots=True)
class BirthdayFacts:
    day: int
    month: int
    days_left: int


@dataclass(frozen=True, slots=True)
class UpupaFacts:
    requests: int
    favorites: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class PersonalFacts:
    activity: ActivityFacts | None = None
    rhythm: RhythmFacts | None = None
    reactions: ReactionFacts | None = None
    crocodile: CrocodileFacts | None = None
    dnd: DndFacts | None = None
    birthday: BirthdayFacts | None = None
    upupa: UpupaFacts | None = None
    words: tuple[tuple[str, int], ...] = ()
    average_words: float | None = None


# --- pure analysis ------------------------------------------------------------

def _normalize_emoji(emoji: str) -> str:
    return str(emoji or "").replace("️", "").replace("︎", "").strip()


def top_reactions(counts: dict[str, int], limit: int = TOP_ITEMS) -> tuple[tuple[str, int], ...]:
    merged: dict[str, int] = {}
    for emoji, count in (counts or {}).items():
        key = _normalize_emoji(emoji)
        if key and int(count) > 0:
            merged[key] = merged.get(key, 0) + int(count)
    return tuple(sorted(merged.items(), key=lambda item: (-item[1], item[0]))[:limit])


def build_rhythm(hours: Sequence[int], weekdays: Sequence[int]) -> RhythmFacts | None:
    """Chronotype from a 24-hour histogram; weekdays use SQLite order (0 = Sunday)."""
    hours = [max(0, int(value)) for value in list(hours)[:24]] + [0] * max(0, 24 - len(hours))
    total = sum(hours)
    if total < RHYTHM_MIN_MESSAGES:
        return None
    night, morning = sum(hours[0:5]) / total, sum(hours[5:11]) / total
    day, evening = sum(hours[11:18]) / total, sum(hours[18:24]) / total
    if night >= 0.2:
        chronotype = "owl"
    elif morning >= 0.35:
        chronotype = "lark"
    else:
        chronotype = "day" if day >= evening else "evening"
    peak_hour = max(range(24), key=lambda hour: (hours[hour], -hour))

    peak_weekday = None
    week = [max(0, int(value)) for value in list(weekdays)[:7]]
    if len(week) == 7 and sum(week):
        sqlite_day = max(range(7), key=lambda index: (week[index], -index))
        if week[sqlite_day] / sum(week) >= PEAK_WEEKDAY_MIN_SHARE:
            peak_weekday = (sqlite_day + 6) % 7
    return RhythmFacts(hours=tuple(hours), chronotype=chronotype, peak_hour=peak_hour, peak_weekday=peak_weekday)


def days_until_birthday(day: int, month: int, today: date) -> int:
    def occurrence(year: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:  # 29 февраля в невисокосный год
            return date(year, 3, 1)

    upcoming = occurrence(today.year)
    if upcoming < today:
        upcoming = occurrence(today.year + 1)
    return (upcoming - today).days


def feature_label(feature: str) -> str | None:
    raw = str(feature or "").strip()
    if not raw or raw in PASSIVE_AI_FEATURES or raw.startswith("канал Упупы"):
        return None
    label = raw.split(":", 1)[0].split(" / ", 1)[0].strip()
    if not label or label == "прочее":
        return None
    return _FEATURE_LABELS.get(label, label)


def build_upupa_usage(rows: Sequence[tuple[str, int]]) -> UpupaFacts | None:
    merged: dict[str, int] = {}
    for feature, count in rows or ():
        label = feature_label(feature)
        if label and int(count) > 0:
            merged[label] = merged.get(label, 0) + int(count)
    if not merged:
        return None
    favorites = tuple(sorted(merged.items(), key=lambda item: (-item[1], item[0]))[:TOP_ITEMS])
    return UpupaFacts(requests=sum(merged.values()), favorites=favorites)


# --- sources ------------------------------------------------------------------

async def _safe(source: str, awaitable):
    try:
        return await awaitable
    except Exception as exc:
        logging.info("[social_graph] dossier source %s unavailable: %s", source, exc)
        return None


async def _load_activity(chat_id: int, user_id: int) -> tuple[ActivityFacts | None, RhythmFacts | None]:
    from features import stat_rank_settings, statistics

    counters, rhythm = await asyncio.gather(
        _safe("rank", stat_rank_settings.get_user_counters(chat_id, user_id)),
        _safe("rhythm", statistics.get_user_chat_rhythm(chat_id, user_id, period_days=RHYTHM_PERIOD_DAYS)),
    )
    rhythm = rhythm or {}
    total = int((counters or {}).get("total", 0) or 0)
    activity = None
    if counters or rhythm.get("last_message"):
        activity = ActivityFacts(
            total=total,
            weekly=int((counters or {}).get("weekly", 0) or 0),
            rank=stat_rank_settings.rank_for_total(total) if total else None,
            last_seen=rhythm.get("last_message"),
        )
    return activity, build_rhythm(rhythm.get("hours") or (), rhythm.get("weekdays") or ())


async def _load_reactions(chat_id: int, user_id: int) -> ReactionFacts | None:
    from features.chronicle.runtime import user_reaction_profile

    profile = await user_reaction_profile(chat_id, user_id)
    facts = ReactionFacts(
        given=top_reactions(profile.get("given") or {}),
        received=top_reactions(profile.get("received") or {}),
        best_message_id=profile.get("best_message_id"),
        best_message_reactions=int(profile.get("best_message_reactions") or 0),
        best_message_text=" ".join(str(profile.get("best_message_text") or "").split()),
    )
    return facts if facts.given or facts.received or facts.best_message_id else None


def _crocodile_sync(chat_id: int, user_id: int) -> CrocodileFacts | None:
    from features.crocodile_scoring import artist_stats
    from games.crocodile_ratings import artist_likes

    row = artist_stats(chat_id, user_id) or {}
    likes = artist_likes(chat_id, user_id)
    draws = int(row.get("successful_draws", 0) or 0)
    if not draws and not likes:
        return None
    timed = int(row.get("timed_draws", 0) or 0)
    average = float(row.get("total_guess_seconds", 0) or 0) / timed if timed else None
    return CrocodileFacts(draws=draws, likes=likes, average_seconds=average)


def _dnd_sync(chat_id: int, user_id: int) -> DndFacts | None:
    from AI import dnd, dnd_campaign

    dnd_campaign._load_archive(dnd)
    history = dnd_campaign._chat_history(chat_id)
    key = str(int(user_id))
    campaigns = [
        row for row in history.get("campaigns") or []
        if isinstance(row, dict) and (key in (row.get("profiles") or {}) or key in (row.get("character_sheets") or {}))
    ]
    player = (history.get("players") or {}).get(key)
    player = player if isinstance(player, dict) else {}
    adventures = [item for item in player.get("adventures") or [] if isinstance(item, dict)]
    if not campaigns and not adventures:
        return None
    profile = player.get("profile") or (campaigns[-1].get("profiles") or {}).get(key) or {}
    last_plot = adventures[-1].get("plot") if adventures else campaigns[-1].get("selected_plot")
    return DndFacts(
        adventures=max(len(campaigns), len(adventures)),
        hero=" ".join(str(profile.get("style") or "").split()) or None,
        archetype=str(profile.get("archetype") or "") or None,
        dead=bool(player.get("dead")),
        last_plot=" ".join(str(last_plot or "").split()) or None,
    )


def _birthday_sync(chat_id: int, user_id: int, today: date) -> BirthdayFacts | None:
    from AI.birthday_calendar import get_chat_birthdays

    row = get_chat_birthdays(chat_id).get(str(int(user_id)))
    if not isinstance(row, dict):
        return None
    day, month = int(row["day"]), int(row["month"])
    return BirthdayFacts(day=day, month=month, days_left=days_until_birthday(day, month, today))


async def _load_upupa(chat_id: int, user_id: int) -> UpupaFacts | None:
    from features.statistics import get_user_ai_usage

    return build_upupa_usage(await get_user_ai_usage(chat_id, user_id, period_days=UPUPA_PERIOD_DAYS))


async def _load_writing(chat_id: int, user_id: int) -> tuple[tuple[tuple[str, int], ...], float | None]:
    """Favourite words and the average message length (only once there are enough messages)."""
    from features.lexicon_settings import writing_profile

    profile = await writing_profile(user_id, chat_id, top_n=TOP_ITEMS)
    average = profile.get("average_words") if profile.get("messages", 0) >= VERBOSITY_MIN_MESSAGES else None
    return tuple(profile.get("favorite_words") or ()), average


_facts_cache: OrderedDict[tuple[int, int], tuple[float, PersonalFacts]] = OrderedDict()


async def load_personal_facts(chat_id: int, user_id: int) -> PersonalFacts:
    """All non-graph facts about one participant; cached briefly for the «Подробнее» button."""
    key = (int(chat_id), int(user_id))
    cached = _facts_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < FACTS_TTL_SECONDS:
        return cached[1]
    today = app_now_naive().date()
    activity_rhythm, reactions, crocodile, dnd, birthday, upupa, writing = await asyncio.gather(
        _safe("activity", _load_activity(*key)),
        _safe("reactions", _load_reactions(*key)),
        _safe("crocodile", asyncio.to_thread(_crocodile_sync, *key)),
        _safe("dnd", asyncio.to_thread(_dnd_sync, *key)),
        _safe("birthday", asyncio.to_thread(_birthday_sync, *key, today)),
        _safe("upupa", _load_upupa(*key)),
        _safe("writing", _load_writing(*key)),
    )
    activity, rhythm = activity_rhythm or (None, None)
    words, average_words = writing or ((), None)
    facts = PersonalFacts(
        activity=activity,
        rhythm=rhythm,
        reactions=reactions,
        crocodile=crocodile,
        dnd=dnd,
        birthday=birthday,
        upupa=upupa,
        words=words,
        average_words=average_words,
    )
    _facts_cache[key] = (time.monotonic(), facts)
    _facts_cache.move_to_end(key)
    while len(_facts_cache) > FACTS_CACHE_SIZE:
        _facts_cache.popitem(last=False)
    return facts


_nickname_cache: dict[tuple[int, int], tuple[float, float, str | None]] = {}


async def dossier_nickname(chat_id: int, user_id: int, name: str, facts: list[str]) -> str | None:
    """AI nickname from dossier facts, cached for a day (a failure is retried sooner)."""
    key = (int(chat_id), int(user_id))
    cached = _nickname_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < cached[1]:
        return cached[2]
    nickname = None
    try:
        with ai_feature_context("досье"):
            nickname = await asyncio.wait_for(invent_nickname(name, facts, str(chat_id)), NICKNAME_TIMEOUT_SECONDS)
    except Exception as exc:
        logging.info("[social_graph] dossier nickname skipped: %s", exc)
    ttl = NICKNAME_TTL_SECONDS if nickname else NICKNAME_FAILURE_TTL_SECONDS
    _nickname_cache[key] = (now, ttl, nickname)
    return nickname


# --- wording ------------------------------------------------------------------

def number(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def messages_phrase(value: int) -> str:
    return f"{number(value)} {_plural(int(value), 'сообщение', 'сообщения', 'сообщений')}"


def silence_days(last_seen: datetime | None, today: date) -> int:
    if last_seen is None:
        return 0
    days = (today - last_seen.date()).days
    return days if days >= SILENCE_MIN_DAYS else 0


def activity_phrase(activity: ActivityFacts, today: date) -> str | None:
    parts = []
    if activity.total:
        parts.append(messages_phrase(activity.total))
    quiet = silence_days(activity.last_seen, today)
    if quiet:
        parts.append(f"молчит {quiet} {_plural(quiet, 'день', 'дня', 'дней')}")
    elif activity.weekly:
        parts.append(f"{number(activity.weekly)} за неделю")
    return " · ".join(parts) or None


def chronotype_title(rhythm: RhythmFacts) -> str:
    return CHRONOTYPES[rhythm.chronotype][1]


def chronotype_emoji(rhythm: RhythmFacts) -> str:
    return CHRONOTYPES[rhythm.chronotype][0]


def rhythm_phrase(rhythm: RhythmFacts, *, compact: bool = False) -> str:
    parts = [chronotype_title(rhythm), f"пик в {rhythm.peak_hour:02d}:00"]
    if rhythm.peak_weekday is not None:
        parts.append(f"{'' if compact else 'чаще '}по {WEEKDAYS_DATIVE[rhythm.peak_weekday]}")
    return " · ".join(parts)


def reaction_word(emoji: str) -> str | None:
    return REACTION_WORDS.get(_normalize_emoji(emoji))


def reactions_card_phrase(reactions: ReactionFacts) -> str | None:
    parts = []
    given = next((reaction_word(emoji) for emoji, _count in reactions.given if reaction_word(emoji)), None)
    received = next((reaction_word(emoji) for emoji, _count in reactions.received if reaction_word(emoji)), None)
    if given:
        parts.append(f"ставит {given}")
    if received:
        parts.append(f"получает {received}")
    return " · ".join(parts) or None


def reactions_emoji_phrase(items: Sequence[tuple[str, int]]) -> str:
    return " ".join(emoji for emoji, _count in items)


def verbosity_style(average_words: float) -> str:
    return next(style for bound, style in reversed(VERBOSITY_STYLES) if average_words >= bound)


def verbosity_phrase(average_words: float) -> str:
    """«пишет коротко, ~5 слов» — manner plus the rounded average."""
    words = max(1, round(average_words))
    return f"пишет {verbosity_style(average_words)}, ~{words} {_plural(words, 'слово', 'слова', 'слов')}"


def words_phrase(words: Sequence[tuple[str, int]], limit: int = 2) -> str | None:
    return " · ".join(f"«{word}»" for word, _count in words[:limit]) or None


def birthday_date(birthday: BirthdayFacts) -> str:
    return f"{birthday.day} {MONTHS_GENITIVE[birthday.month - 1]}"


def birthday_countdown(birthday: BirthdayFacts) -> str:
    if birthday.days_left == 0:
        return "сегодня!"
    if birthday.days_left == 1:
        return "завтра"
    return f"через {birthday.days_left} {_plural(birthday.days_left, 'день', 'дня', 'дней')}"


def duration_phrase(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} мин {rest} с" if rest else f"{minutes} мин"


def crocodile_phrase(crocodile: CrocodileFacts) -> str | None:
    parts = []
    if crocodile.draws:
        parts.append(f"угадали {crocodile.draws} {_plural(crocodile.draws, 'рисунок', 'рисунка', 'рисунков')}")
    if crocodile.likes:
        parts.append(f"{crocodile.likes} {_plural(crocodile.likes, 'лайк', 'лайка', 'лайков')}")
    return " · ".join(parts) or None


def dnd_phrase(dnd: DndFacts) -> str:
    parts = [f"{dnd.adventures} {_plural(dnd.adventures, 'приключение', 'приключения', 'приключений')}"]
    if dnd.dead:
        parts.append("последний герой погиб")
    elif dnd.hero:
        parts.append(dnd.hero)
    return " · ".join(parts)


def upupa_phrase(upupa: UpupaFacts) -> str:
    phrase = f"{upupa.requests} {_plural(upupa.requests, 'просьба', 'просьбы', 'просьб')}"
    if upupa.favorites:
        phrase += f" · чаще «{upupa.favorites[0][0]}»"
    return phrase
