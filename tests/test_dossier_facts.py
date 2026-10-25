from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import date, datetime, timedelta
import sqlite3

from tests import test_smoke_imports  # noqa: F401

from features.social_graph import dossier_facts
from features.social_graph.ai import invent_nickname
from infrastructure.persistence.sqlite_statistics import SQLiteStatisticsRepository
from tests.test_chronicle import build_stores


# --- pure analysis ------------------------------------------------------------

def test_rhythm_chronotypes_peaks_and_threshold():
    owl = [0] * 24
    owl[1], owl[2], owl[15] = 10, 8, 12
    rhythm = dossier_facts.build_rhythm(owl, [0, 0, 0, 0, 0, 30, 0])
    assert (rhythm.chronotype, rhythm.peak_hour, rhythm.peak_weekday) == ("owl", 15, 4)
    assert dossier_facts.rhythm_phrase(rhythm) == "сова · пик в 15:00 · чаще по пятницам"

    lark = [0] * 24
    lark[7], lark[13] = 20, 15
    assert dossier_facts.build_rhythm(lark, [5] * 7).chronotype == "lark"
    assert dossier_facts.build_rhythm(lark, [5] * 7).peak_weekday is None  # ровная неделя без пика

    evening = [0] * 24
    evening[13], evening[21] = 8, 20
    assert dossier_facts.build_rhythm(evening, []).chronotype == "evening"
    assert dossier_facts.build_rhythm([1] * 10, []) is None


def test_birthday_countdown_wraps_year_and_leap_day():
    today = date(2026, 10, 25)
    assert dossier_facts.days_until_birthday(25, 10, today) == 0
    assert dossier_facts.days_until_birthday(26, 10, today) == 1
    assert dossier_facts.days_until_birthday(24, 10, today) == 364
    assert dossier_facts.days_until_birthday(29, 2, today) == (date(2027, 3, 1) - today).days
    birthday = dossier_facts.BirthdayFacts(day=1, month=4, days_left=23)
    assert dossier_facts.birthday_date(birthday) == "1 апреля"
    assert dossier_facts.birthday_countdown(birthday) == "через 23 дня"


def test_upupa_usage_skips_passive_features_and_merges_variants():
    usage = dossier_facts.build_upupa_usage([
        ("нарисуй", 5), ("нарисуй: GigaChat", 2), ("диалог", 4),
        ("случайные AI-реакции", 50), ("канал Упупы: пост", 3), ("прочее: генерация текста", 9),
        ("пиздиш / фактчек", 1),
    ])
    assert usage.requests == 12
    assert usage.favorites == (("нарисуй", 7), ("поболтать", 4), ("пиздиш", 1))
    assert dossier_facts.upupa_phrase(usage) == "12 просьб · чаще «нарисуй»"
    assert dossier_facts.build_upupa_usage([("проактивный вброс", 3)]) is None


def test_reactions_are_normalized_and_named_for_the_card():
    top = dossier_facts.top_reactions({"❤️": 3, "❤": 2, "🔥": 4, "🫥": 9})
    assert top == (("🫥", 9), ("❤", 5), ("🔥", 4))
    facts = dossier_facts.ReactionFacts(given=top, received=(("🤣", 7),))
    # 🫥 has no word on the card, so the next known reaction is shown.
    assert dossier_facts.reactions_card_phrase(facts) == "ставит сердечко · получает ржач"


def test_activity_phrase_reports_silence():
    activity = dossier_facts.ActivityFacts(total=21, weekly=0, last_seen=datetime(2026, 10, 13, 12))
    assert dossier_facts.activity_phrase(activity, date(2026, 10, 25)) == "21 сообщение · молчит 12 дней"
    assert dossier_facts.since_phrase(datetime(2025, 3, 2)) == "в чате с марта 2025"


# --- repositories -------------------------------------------------------------

def test_statistics_rhythm_and_ai_usage_are_scoped_to_chat_and_user(tmp_path):
    repository = SQLiteStatisticsRepository(tmp_path / "statistics.db")
    repository.init_schema()
    base = (datetime.now() - timedelta(days=2)).replace(hour=1, minute=0, second=0, microsecond=0)
    rows = [
        (-100, 1, base, 0),
        (-100, 1, base + timedelta(hours=13), 0),
        (-100, 1, base + timedelta(hours=13, minutes=5), 0),
        (-100, 1, base - timedelta(days=400), 0),  # старше окна ритма, но задаёт «в чате с»
        (-100, 2, base, 0),
        (-200, 1, base, 0),
        (-100, 1, base, 1),
    ]
    with closing(sqlite3.connect(tmp_path / "statistics.db")) as conn, conn:
        conn.executemany(
            "INSERT INTO message_stats (chat_id, user_id, message_timestamp, message_type, is_private) VALUES (?, ?, ?, 'text', ?)",
            rows,
        )
    rhythm = repository.get_user_chat_rhythm(-100, 1, period_days=90)
    assert sum(rhythm["hours"]) == 3 and rhythm["hours"][1] == 1 and rhythm["hours"][14] == 2
    assert rhythm["first_message"].date() == (base - timedelta(days=400)).date()
    assert sum(rhythm["weekdays"]) == 3

    minute = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    calls = [
        (-100, 1, "нарисуй", f"{minute}:01", 1),
        (-100, 1, "нарисуй", f"{minute}:30", 1),
        (-100, 1, "нарисуй", f"{minute}:59", None),
        (-100, 1, "диалог", f"{minute}:10", 1),
        (-100, 1, "диалог", f"{minute}:20", 0),  # failed call
        (-100, 2, "нарисуй", f"{minute}:01", 1),
        (-200, 1, "нарисуй", f"{minute}:01", 1),
    ]
    with closing(sqlite3.connect(tmp_path / "statistics.db")) as conn, conn:
        conn.executemany(
            "INSERT INTO model_stats (chat_id, user_id, model_name, request_type, feature, timestamp, success) VALUES (?, ?, 'model', 'text', ?, ?, ?)",
            calls,
        )
    # Three calls within one minute are one request.
    assert repository.get_user_ai_usage(-100, 1) == [("диалог", 1), ("нарисуй", 1)]


def test_reaction_profile_counts_given_received_and_best_message(tmp_path):
    candidates, _events, _backfill = build_stores(tmp_path)
    with closing(sqlite3.connect(tmp_path / "history.db")) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS history_messages (id INTEGER PRIMARY KEY, chat_id TEXT, user_id TEXT, message_id INTEGER, text TEXT)")
        conn.executemany(
            "INSERT INTO history_messages (chat_id, user_id, message_id, text) VALUES (?, ?, ?, ?)",
            [("-100", "1", 10, "обычное"), ("-100", "1", 11, "хит дня"), ("-100", "2", 20, "чужое")],
        )
    now = datetime.now()
    candidates.record_user_reaction(-100, 10, 2, ["👍"], now)
    candidates.record_user_reaction(-100, 11, 2, ["🤣", "🔥"], now)
    candidates.record_user_reaction(-100, 11, 3, ["🤣"], now)
    candidates.record_user_reaction(-100, 11, 1, ["❤"], now)  # self-reaction is not «received»
    candidates.record_user_reaction(-100, 20, 1, ["🔥"], now)
    candidates.record_aggregate_reaction(-100, 10, 2, 1, now)

    profile = candidates.user_reaction_profile(-100, 1)

    assert profile["given"] == {"❤": 1, "🔥": 1}
    assert profile["received"] == {"👍": 1, "🤣": 2, "🔥": 1}
    assert (profile["best_message_id"], profile["best_message_reactions"], profile["best_message_text"]) == (11, 3, "хит дня")
    assert candidates.user_reaction_profile(-100, 9)["best_message_id"] is None


def test_favorite_words_skip_bot_calls_and_stop_at_scan_limit(monkeypatch):
    from features import lexicon_settings

    class Repository:
        def scan(self, chat_id, visitor, *, descending=False, **filters):
            assert (chat_id, descending, filters) == (-100, True, {"user_id": 1})
            for text in ["упупа кондей", "кондей кондей ну", "42 42 42", "короче кондей", "сцена"] * 3:
                if visitor({"text": text}) is False:
                    break

    monkeypatch.setattr(lexicon_settings, "get_history_repository", lambda _path: Repository())
    words = asyncio.run(lexicon_settings.favorite_words(1, -100, top_n=3, scan_limit=4))
    assert words == [("кондей", 4), ("короче", 1)]


# --- composition and AI -------------------------------------------------------

def test_personal_facts_survive_a_broken_source(monkeypatch):
    dossier_facts._facts_cache.clear()

    async def activity(_chat_id, _user_id):
        return dossier_facts.ActivityFacts(total=10), None

    async def broken(_chat_id, _user_id):
        raise RuntimeError("chronicle is down")

    async def words(_chat_id, _user_id):
        return (("кондей", 3),)

    async def nothing(_chat_id, _user_id):
        return None

    monkeypatch.setattr(dossier_facts, "_load_activity", activity)
    monkeypatch.setattr(dossier_facts, "_load_reactions", broken)
    monkeypatch.setattr(dossier_facts, "_load_upupa", nothing)
    monkeypatch.setattr(dossier_facts, "_load_words", words)
    monkeypatch.setattr(dossier_facts, "_crocodile_sync", lambda *_args: dossier_facts.CrocodileFacts(draws=2))
    monkeypatch.setattr(dossier_facts, "_dnd_sync", lambda *_args: None)
    monkeypatch.setattr(dossier_facts, "_birthday_sync", lambda *_args: None)

    facts = asyncio.run(dossier_facts.load_personal_facts(-100, 1))

    assert facts.activity.total == 10 and facts.reactions is None
    assert facts.crocodile.draws == 2 and facts.words == (("кондей", 3),)
    assert asyncio.run(dossier_facts.load_personal_facts(-100, 1)) is facts  # cached for the details button
    dossier_facts._facts_cache.clear()


def test_nickname_is_cleaned_and_cached(monkeypatch):
    async def generator(_prompt, _chat_id):
        return "«Повелитель кондея».\nПотому что любит кондей"

    assert asyncio.run(invent_nickname("Женя", ["любимые слова: кондей"], "-100", generator=generator)) == "Повелитель кондея"

    async def failure(_prompt, _chat_id):
        return "Ошибка блять, ничего не вышло"

    assert asyncio.run(invent_nickname("Женя", ["факт"], "-100", generator=failure)) is None
    assert asyncio.run(invent_nickname("Женя", [], "-100", generator=generator)) is None

    calls = []

    async def fake_invent(name, facts, chat_id):
        calls.append(name)
        return "Король войсов"

    dossier_facts._nickname_cache.clear()
    monkeypatch.setattr(dossier_facts, "invent_nickname", fake_invent)
    first = asyncio.run(dossier_facts.dossier_nickname(-100, 1, "Женя", ["факт"]))
    second = asyncio.run(dossier_facts.dossier_nickname(-100, 1, "Женя", ["факт"]))
    assert first == second == "Король войсов" and calls == ["Женя"]
    dossier_facts._nickname_cache.clear()
