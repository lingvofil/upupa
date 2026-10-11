from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401

from core import chronicle_sink
from features.social_graph import relationships
from features.social_graph.dynamics import SPARK_EMPTY, SPARK_WEEKS, build_dynamics, sparkline
from features.social_graph.reactions import emoji_valence, reaction_items_valence
from features.social_graph.relationship_history import assemble_relationship_history
from infrastructure.persistence.sqlite_social_graph import SQLiteSocialGraphRepository
from tests.test_relationship_history import _event, _view


# --- command matching -------------------------------------------------------

def _msg(text):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=1, is_bot=False))


def test_pair_commands_ignore_ordinary_sentences():
    import handlers.social_graph as handler

    assert handler._is_pair_command(_msg("история"), "история отношений", "история")
    assert handler._is_pair_command(_msg("История @Bob"), "история отношений", "история")
    assert handler._is_pair_command(_msg("история отношений @a_1 @bob"), "история отношений", "история")
    assert not handler._is_pair_command(_msg("история такая, короче"), "история отношений", "история")
    assert not handler._is_pair_command(_msg("история @bob и вообще"), "история отношений", "история")

    assert handler._is_pair_command(_msg("отношения"), "отношения")
    assert handler._is_pair_command(_msg("отношения @alice @bob"), "отношения")
    assert not handler._is_pair_command(_msg("отношения чата"), "отношения")
    assert not handler._is_pair_command(_msg("отношения у нас сложные"), "отношения")


# --- reaction valence -------------------------------------------------------

def test_reaction_valence_is_conservative():
    assert emoji_valence("❤️") == 1
    assert emoji_valence("🤡") == -1
    assert emoji_valence("🤔") == 0
    items = [SimpleNamespace(emoji="👍"), SimpleNamespace(emoji="🤡")]
    assert reaction_items_valence(items) == -1
    assert reaction_items_valence([SimpleNamespace(custom_emoji_id="1")]) == 0


def test_repository_counts_valenced_reactions_and_migrates_old_schema(tmp_path):
    path = tmp_path / "stats.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE social_relationships (
            chat_id BIGINT NOT NULL, user_a_id BIGINT NOT NULL, user_b_id BIGINT NOT NULL,
            xp REAL NOT NULL DEFAULT 0, interaction_count INTEGER NOT NULL DEFAULT 0,
            reply_count INTEGER NOT NULL DEFAULT 0, mention_count INTEGER NOT NULL DEFAULT 0,
            reaction_count INTEGER NOT NULL DEFAULT 0, a_to_b_count INTEGER NOT NULL DEFAULT 0,
            b_to_a_count INTEGER NOT NULL DEFAULT 0, a_to_b_weight REAL NOT NULL DEFAULT 0,
            b_to_a_weight REAL NOT NULL DEFAULT 0, first_interaction_at TEXT, last_interaction_at TEXT,
            updated_at TEXT NOT NULL, PRIMARY KEY (chat_id, user_a_id, user_b_id), CHECK (user_a_id < user_b_id))"""
    )
    conn.commit()
    conn.close()

    repo = SQLiteSocialGraphRepository(path)
    repo.init_schema()
    now = datetime.now(timezone.utc)
    assert repo.record_interaction(-100, (1, "A", "a"), 2, "reaction", 1, now, 1.0, valence=-1)
    assert repo.record_interaction(-100, (2, "B", "b"), 1, "reaction", 2, now, 1.0, valence=1)
    assert repo.record_interaction(-100, (2, "B", "b"), 1, "reaction", 3, now, 1.0)

    row = repo.load_relationship_states(-100)[0]
    assert row["negative_reaction_count"] == 1
    assert row["positive_reaction_count"] == 1
    assert row["reaction_count"] == 3

    daily = repo.load_relationship_daily(-100, 2, 1)
    assert len(daily) == 1 and daily[0]["event_count"] == 3
    assert repo.list_active_chats(now - timedelta(days=1)) == [-100]
    assert repo.list_active_chats(now + timedelta(days=1)) == []


def _row(**overrides):
    row = {
        "user_a_id": 1, "user_b_id": 2, "user_a_name": "A", "user_b_name": "B",
        "xp": 30.0, "interaction_count": 30, "reply_count": 10, "mention_count": 5,
        "reaction_count": 15, "a_to_b_count": 15, "b_to_a_count": 15,
        "a_to_b_weight": 30.0, "b_to_a_weight": 30.0, "recent_7d": 5, "previous_21d": 15,
        "last_interaction_at": datetime.now(timezone.utc).isoformat(),
    }
    row.update(overrides)
    return row


def test_negative_reactions_raise_tension_without_chronicle(monkeypatch):
    rows = [_row()]

    async def fake_rows(chat_id, *, user_id=None):
        return rows

    async def no_events(chat_id, *, limit):
        return []

    async def forbidden_snapshot(view):
        raise AssertionError("reads must not persist snapshots")

    monkeypatch.setattr(relationships, "load_relationship_states", fake_rows)
    monkeypatch.setattr(relationships, "list_chronicle_events", no_events)
    monkeypatch.setattr(relationships, "save_relationship_snapshot", forbidden_snapshot)

    calm = asyncio.run(relationships.get_relationships(-100))[0]
    assert calm.tension == 0

    rows[:] = [_row(negative_reaction_count=12, positive_reaction_count=2)]
    trolls = asyncio.run(relationships.get_relationships(-100))[0]
    assert 20 <= trolls.tension <= 60
    assert trolls.negative_reaction_count == 12
    assert trolls.archetype == "взаимный троллинг"


def test_snapshot_job_persists_only_enabled_active_chats(monkeypatch):
    saved = []

    async def active(*, days):
        return [-1, -2]

    async def fake_get(chat_id, *, persist_snapshots=False):
        saved.append((chat_id, persist_snapshots))
        return [object(), object()]

    monkeypatch.setattr(relationships, "list_active_chats", active)
    monkeypatch.setattr(relationships, "is_social_graph_enabled", lambda chat_id: chat_id == -1)
    monkeypatch.setattr(relationships, "get_relationships", fake_get)

    assert asyncio.run(relationships.capture_relationship_snapshots()) == 2
    assert saved == [(-1, True)]


# --- dynamics ---------------------------------------------------------------

def _rows(days: dict[date, int]):
    return [{"activity_date": day.isoformat(), "event_count": count, "xp_earned": 1.0} for day, count in days.items()]


def test_sparkline_marks_empty_weeks():
    assert sparkline([0, 1, 4, 8]) == f"{SPARK_EMPTY}▁▄█"
    assert sparkline([0, 0]) == SPARK_EMPTY * 2


def test_dynamics_detects_comeback_streak_peak_and_phase():
    today = date(2026, 10, 11)
    days = {}
    start = today - timedelta(days=120)
    for offset in range(7):  # a week-long streak with a busy week
        days[start + timedelta(days=offset)] = 3
    days[start + timedelta(days=60)] = 1  # pause of 54 days before this
    for offset in range(1, 15):  # recent activity ramp
        days[today - timedelta(days=offset)] = 2

    dynamics = build_dynamics(_rows(days), today=today)

    assert len(dynamics.sparkline) == SPARK_WEEKS
    kinds = {item.kind for item in dynamics.milestones}
    assert {"dynamics_comeback", "dynamics_streak", "dynamics_peak"} <= kinds
    comeback = next(item for item in dynamics.milestones if item.kind == "dynamics_comeback")
    assert "54 дня" in comeback.summary
    assert dynamics.phase == "на подъёме"
    assert dynamics.total_events == 3 * 7 + 1 + 2 * 14


def test_dynamics_phase_for_new_and_silent_pairs():
    today = date(2026, 10, 11)
    assert build_dynamics([], today=today).phase == "пока без истории"
    assert build_dynamics(_rows({today - timedelta(days=3): 4}), today=today).phase == "знакомятся"
    silent = {today - timedelta(days=90 + offset): 2 for offset in range(10)}
    assert build_dynamics(_rows(silent), today=today).phase == "затишье"


def test_dynamics_milestones_join_history_but_never_crowd_out_chronicle():
    today = date(2026, 5, 20)
    days = {date(2026, 1, 5) + timedelta(days=offset): 3 for offset in range(6)}
    days[date(2026, 5, 10)] = 2
    dynamics = build_dynamics(_rows(days), today=today)

    chronicle = _event(when=datetime(2026, 5, 10, 12, tzinfo=timezone.utc))
    history = assemble_relationship_history(_view((chronicle,)), (), (), dynamics)

    sources = [item.source for item in history.timeline]
    assert "relationship_dynamics" in sources
    assert any(item.chronicle_event is chronicle for item in history.timeline)
    # The comeback on the Chronicle day is covered by the real event instead.
    assert not any(
        item.event_type == "dynamics_comeback" and item.timestamp.date() == date(2026, 5, 10)
        for item in history.timeline
    )
    assert [item.timestamp for item in history.timeline] == sorted(item.timestamp for item in history.timeline)
    assert history.dynamics is dynamics


# --- Chronicle sink and external sources ------------------------------------

def test_chronicle_sink_is_noop_until_configured_and_never_raises():
    assert chronicle_sink.report_chronicle_event(chat_id=-1, context="x", source="t") is False

    calls = []

    async def failing(**payload):
        calls.append(payload)
        raise RuntimeError("boom")

    async def scenario():
        chronicle_sink.configure_chronicle_sink(failing)
        assert chronicle_sink.report_chronicle_event(chat_id=-1, context="  событие ", source="test") is True
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert calls and calls[0]["context"] == "событие" and calls[0]["source"] == "test"


def test_crocodile_reports_only_milestones_and_new_leader(monkeypatch):
    from games import crocodile

    reports = []
    monkeypatch.setattr(crocodile, "_scores_save", lambda: None)
    monkeypatch.setattr(crocodile, "report_chronicle_event", lambda **kwargs: reports.append(kwargs))
    monkeypatch.setattr(
        crocodile,
        "_scores",
        {"-100": {"1": {"pts": 12, "name": "Alice"}, "2": {"pts": 12, "name": "Bob"}, "3": {"pts": 3, "name": "Carl"}}},
    )

    crocodile.add_point("-100", 3, "Carl")
    assert reports == []

    crocodile.add_point("-100", 2, "Bob")  # 13 > 12: Bob overtakes the previous leader Alice
    assert len(reports) == 1
    assert "вышел на первое место" in reports[0]["context"]
    assert reports[0]["source"] == "crocodile"

    crocodile._scores["-100"]["3"]["pts"] = 24
    crocodile.add_point("-100", 3, "Carl")
    assert "25 очков" in reports[-1]["context"]


def test_court_verdict_is_reported_with_both_parties(monkeypatch):
    from features import court

    reports = []
    monkeypatch.setattr(court, "report_chronicle_event", lambda **kwargs: reports.append(kwargs))
    plaintiff = SimpleNamespace(id=1, is_bot=False, full_name="Alice", username="alice")
    defendant = SimpleNamespace(id=2, is_bot=False, full_name="Bob", username=None)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100),
        message_id=50,
        from_user=plaintiff,
        reply_to_message=SimpleNamespace(message_id=40, from_user=defendant),
    )

    court._report_case_to_chronicle(message, {
        "case_id": 7, "requester_name": "Alice", "target_name": "Bob",
        "parties": ["Alice", "Bob"], "claim": "", "verdict": "Решение: оба виноваты.",
    })

    report = reports[0]
    assert report["source"] == "court"
    assert [p["id"] for p in report["participants"]] == [1, 2]
    assert report["anchor_message_id"] == 40
    assert "дело №7" in report["context"] and "оба виноваты" in report["context"]


def test_dnd_finale_is_reported_with_players(monkeypatch):
    from AI import dnd_campaign

    reports = []
    monkeypatch.setattr(chronicle_sink, "report_chronicle_event", lambda **kwargs: reports.append(kwargs))
    session = SimpleNamespace(
        chat_id=-100,
        selected_plot="Побег из санатория",
        mission_outcome="success",
        participants={"a": {"user_id": 1, "name": "Alice"}, "b": {"user_id": 2, "name": "Bob"}},
    )

    dnd_campaign._report_campaign_to_chronicle(session, "Финал", "Эпилог")

    report = reports[0]
    assert report["source"] == "dnd"
    assert report["base_score"] == 7.0
    assert "миссия выполнена" in report["context"]
    assert [p["id"] for p in report["participants"]] == [1, 2]
