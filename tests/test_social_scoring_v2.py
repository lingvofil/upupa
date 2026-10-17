from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3

from tests import test_smoke_imports  # noqa: F401

from features.chronicle.models import ChronicleEvent
from features.social_graph import relationships
from features.social_graph.reactions import reaction_names_valence
from infrastructure.persistence.sqlite_social_graph import SQLiteSocialGraphRepository


NOW = datetime.now(timezone.utc)


def _event(event_id, *, participants, category="conflict", age_days=1, importance=9.0):
    when = NOW - timedelta(days=age_days)
    return ChronicleEvent(
        id=event_id, chat_id=-100, created_at=when, event_started_at=when,
        event_ended_at=when + timedelta(minutes=20), title="Срач", summary="Был срач.",
        category=category, importance_score=importance, participant_ids=list(participants),
        participant_names=[f"U{p}" for p in participants], participant_usernames=[None for _ in participants],
        source_message_ids=[1], anchor_message_id=1, reaction_count=0, unique_reactors=0, reply_count=3,
        keywords=[], entities=[], related_event_ids=[], ai_metadata={}, source="live",
    )


def _row(a, b, *, xp=40.0, interactions=40, **extra):
    row = {
        "user_a_id": a, "user_b_id": b, "user_a_name": f"U{a}", "user_b_name": f"U{b}",
        "xp": xp, "interaction_count": interactions, "reply_count": interactions // 2,
        "mention_count": 2, "reaction_count": 2, "a_to_b_count": interactions // 2,
        "b_to_a_count": interactions // 2, "a_to_b_weight": 30.0, "b_to_a_weight": 30.0,
        "recent_7d": 3, "previous_21d": 9, "last_interaction_at": NOW.isoformat(),
    }
    row.update(extra)
    return row


def _install(monkeypatch, rows, events, *, exchanges=None, daily=None):
    async def fake_rows(chat_id, *, user_id=None):
        return rows

    async def fake_events(chat_id, *, limit):
        return events

    async def fake_daily(chat_id, since):
        return daily or []

    async def fake_exchanges(chat_id, windows):
        if exchanges is None:
            raise RuntimeError("raw interactions unavailable")
        return {key: value for key, value in exchanges.items() if key in {w[0] for w in windows}}

    monkeypatch.setattr(relationships, "load_relationship_states", fake_rows)
    monkeypatch.setattr(relationships, "list_chronicle_events", fake_events)
    monkeypatch.setattr(relationships, "load_chat_daily", fake_daily)
    monkeypatch.setattr(relationships, "load_pair_exchanges", fake_exchanges)


def _by_pair(views):
    return {(view.user_a_id, view.user_b_id): view for view in views}


# --- tension -------------------------------------------------------------------

def test_group_conflict_raises_tension_only_for_people_who_argued(monkeypatch):
    rows = [_row(1, 2), _row(3, 4), _row(1, 3)]
    conflict = _event("c1", participants=(1, 2, 3, 4, 5))
    _install(monkeypatch, rows, [conflict], exchanges={"c1": {(1, 2): 4, (1, 3): 1}})

    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))

    assert views[(1, 2)].tension > 0 and views[(1, 2)].conflict_episodes == 1
    assert views[(1, 3)].tension == 0  # a single reply in a busy thread is not a fight
    assert views[(3, 4)].tension == 0  # were in the thread, never addressed each other
    assert views[(3, 4)].archetype != "заклятые друзья"


def test_only_main_combatants_of_a_busy_episode_get_tension(monkeypatch):
    rows = [_row(1, 2), _row(2, 3), _row(1, 3)]
    conflict = _event("busy", participants=(1, 2, 3))
    # 1↔2 argued (10 exchanges); 2↔3 also talked a lot (6); 1↔3 chatted a bit (3).
    _install(monkeypatch, rows, [conflict], exchanges={"busy": {(1, 2): 10, (2, 3): 6, (1, 3): 3}})

    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))

    assert views[(1, 2)].tension > views[(2, 3)].tension > 0
    assert views[(1, 3)].tension == 0


def test_core_pairs_chatting_as_usual_during_a_conflict_get_no_tension(monkeypatch):
    rows = [_row(1, 2), _row(3, 4), _row(1, 3), _row(2, 4), _row(1, 4), _row(2, 3)]
    conflict = _event("noisy", participants=(1, 2, 3, 4))
    # Everybody replies to everybody as usual, except 1↔2 who suddenly go at each other.
    window = {(1, 2): 12, (3, 4): 4, (1, 3): 4, (2, 4): 3, (1, 4): 3, (2, 3): 4}
    usual = {(1, 2): 100, (3, 4): 100, (1, 3): 100, (2, 4): 100, (1, 4): 100, (2, 3): 100}
    _install(monkeypatch, rows, [conflict], exchanges={"noisy": window, "__baseline__": usual})

    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))

    assert views[(1, 2)].tension > 0
    assert all(views[pair].tension == 0 for pair in usual if pair != (1, 2))

    # The same evenly spread chatter with no spike gives nobody tension.
    flat = {pair: 5 for pair in usual}
    _install(monkeypatch, rows, [conflict], exchanges={"noisy": flat, "__baseline__": usual})
    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))
    assert all(view.tension == 0 for view in views.values())


def test_old_group_conflict_without_raw_evidence_counts_only_a_share(monkeypatch):
    rows = [_row(1, 2), _row(3, 4)]
    pair_fight = _event("old-pair", participants=(1, 2), age_days=200)
    crowd_fight = _event("old-crowd", participants=(3, 4, 5, 6, 7, 8), age_days=200)
    _install(monkeypatch, rows, [pair_fight, crowd_fight], exchanges={})

    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))

    assert views[(1, 2)].tension > 0
    assert views[(3, 4)].tension == 0  # old crowded episode: impossible to tell who fought whom


def test_missing_evidence_store_trusts_only_small_episodes(monkeypatch):
    rows = [_row(1, 2), _row(3, 4)]
    events = [_event("crowd", participants=(1, 2, 5, 6, 7, 8)), _event("trio", participants=(3, 4, 9))]
    _install(monkeypatch, rows, events, exchanges=None)
    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))
    assert views[(1, 2)].tension == 0
    assert 0 < views[(3, 4)].tension < 40


def test_reactions_alone_cannot_make_sworn_friends(monkeypatch):
    rows = [_row(1, 2, negative_reaction_count=200, positive_reaction_count=0), _row(3, 4, negative_reaction_count=2)]
    _install(monkeypatch, rows, [], exchanges={})
    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))
    assert 25 <= views[(1, 2)].tension <= 35
    assert views[(1, 2)].archetype != "заклятые друзья"
    assert views[(3, 4)].tension == 0  # a couple of 🤡 is a joke, not tension


def test_crowded_episodes_do_not_invent_relationships(monkeypatch):
    _install(monkeypatch, [], [_event("crowd", participants=(1, 2, 3, 4), category="funny"),
                               _event("duo", participants=(7, 8), category="funny")])
    pairs = set(_by_pair(asyncio.run(relationships.get_relationships(-100))))
    assert pairs == {(7, 8)}


# --- closeness and level ---------------------------------------------------------

def test_recent_activity_beats_old_activity():
    old = [{"user_a_id": 1, "user_b_id": 2, "activity_date": (NOW - timedelta(days=120)).date().isoformat(), "xp_earned": 12.0}]
    new = [{"user_a_id": 3, "user_b_id": 4, "activity_date": (NOW - timedelta(days=2)).date().isoformat(), "xp_earned": 12.0}]
    xp = relationships._recent_xp_by_pair(old + new, NOW)
    assert xp[(3, 4)] > 10 * xp[(1, 2)]


def test_levels_are_relative_and_fifteen_is_rare(monkeypatch):
    rows = []
    daily = []
    for index in range(30):
        a, b = index * 2 + 1, index * 2 + 2
        rows.append(_row(a, b, xp=200.0, interactions=200))
        for day in range(0, 60, 3):
            daily.append({
                "user_a_id": a, "user_b_id": b,
                "activity_date": (NOW - timedelta(days=day)).date().isoformat(),
                "xp_earned": 0.3 + index * 0.4,
            })
    _install(monkeypatch, rows, [], exchanges={}, daily=daily)

    views = asyncio.run(relationships.get_relationships(-100))
    levels = [view.level for view in views]

    assert levels.count(100) <= 1
    assert len(set(levels)) >= 20  # visibly different numbers
    assert 35 <= sorted(levels)[len(levels) // 2] <= 65
    assert sorted(view.rank for view in views) == list(range(1, 31))
    by_pair = _by_pair(views)
    assert by_pair[(59, 60)].level > by_pair[(29, 30)].level > by_pair[(1, 2)].level


def test_user_view_is_ranked_against_the_whole_chat(monkeypatch):
    rows = [_row(1, 2, xp=5.0, interactions=5), *[_row(10 + i, 50 + i, xp=300.0, interactions=300) for i in range(10)]]
    _install(monkeypatch, rows, [], exchanges={})

    mine = asyncio.run(relationships.get_relationships(-100, user_id=1))
    assert [(v.user_a_id, v.user_b_id) for v in mine] == [(1, 2)]
    assert mine[0].level <= 35  # weak compared with the chat, even though it is the user's only pair
    assert (mine[0].rank, mine[0].rank_total) == (11, 11)


def test_pairs_above_the_closeness_cap_still_differ():
    base = relationships.RelationshipView(
        chat_id=-1, user_a_id=1, user_b_id=2, user_a_name="A", user_b_name="B", affinity=100, tension=0, xp=500.0,
        level=0, reciprocity=1.0, archetype="x", trend="y", interaction_count=500, reply_count=1, mention_count=1,
        reaction_count=1, shared_event_count=0, a_to_b_count=1, b_to_a_count=1, recent_7d=1, previous_21d=1,
        last_interaction_at=None, shared_events=(),
    )
    views = [
        relationships.replace(base, user_b_id=index + 2, score=100.0 + index)
        for index in range(10)
    ]
    levels = [view.level for view in relationships.assign_relative_levels(views)]
    assert len(set(levels)) == 10
    assert levels[-1] == 100


def test_percentiles_handle_ties():
    assert relationships._percentiles([5, 5, 5]) == [0.5, 0.5, 0.5]
    assert relationships._percentiles([1, 3, 2]) == [0.0, 1.0, 0.5]
    assert relationships._percentiles([7]) == [1.0]


def test_pre_v2_snapshots_are_ignored(monkeypatch):
    async def fake_view(*_args):
        return None

    async def fake_snapshots(chat_id, a, b, *, limit):
        base = {"affinity": 50, "tension": 0, "xp": 1.0, "level": 15, "reciprocity": 1.0, "archetype": "x", "trend": "y"}
        return [
            {**base, "captured_at": "2026-10-01T10:00:00+00:00"},
            {**base, "captured_at": "2026-10-12T10:00:00+00:00", "level": 8},
        ]

    monkeypatch.setattr(relationships, "get_relationship", fake_view)
    monkeypatch.setattr(relationships, "load_relationship_snapshots", fake_snapshots)
    _view, snapshots = asyncio.run(relationships.get_relationship_history(-100, 1, 2))
    assert [s.level for s in snapshots] == [8]


# --- repository ------------------------------------------------------------------

def test_pair_exchanges_count_both_directions_inside_window(tmp_path):
    repo = SQLiteSocialGraphRepository(tmp_path / "stats.db")
    repo.init_schema()
    t0 = NOW - timedelta(days=1)
    bundles = [
        (1, t0, (1, "A", None), 2, "reply"),
        (2, t0 + timedelta(minutes=1), (2, "B", None), 1, "reply"),
        (3, t0 + timedelta(minutes=2), (3, "C", None), 1, "mention"),
        (4, t0 + timedelta(hours=3), (1, "A", None), 2, "reply"),  # outside window
    ]
    for message_id, when, actor, target, kind in bundles:
        repo.record_message_bundle(-100, message_id, when, actor, ((target, "X", None),), ((target, kind, 3.0),))

    result = repo.load_pair_exchanges(-100, [("e1", t0 - timedelta(minutes=5), t0 + timedelta(minutes=30))])
    assert result == {"e1": {(1, 2): 2, (1, 3): 1}}


def test_reaction_valence_backfill_is_bounded_and_idempotent(tmp_path):
    stats = tmp_path / "stats.db"
    history = tmp_path / "history.db"
    repo = SQLiteSocialGraphRepository(stats)
    repo.init_schema()
    now = NOW
    # Bob authored messages 10 and 11; Alice authored 12.
    repo.record_message_bundle(-100, 10, now, (2, "Bob", None), ((1, "Alice", None),), ((1, "reply", 3.0),))
    repo.record_message_bundle(-100, 11, now, (2, "Bob", None), (), ())
    repo.record_message_bundle(-100, 12, now, (1, "Alice", None), ((2, "Bob", None),), ((2, "reply", 3.0),))

    conn = sqlite3.connect(history)
    conn.execute(
        "CREATE TABLE chronicle_user_reactions (chat_id INTEGER, message_id INTEGER, user_id INTEGER, reactions_json TEXT, updated_at TEXT)"
    )
    rows = [
        (-100, 10, 1, json.dumps(["🤡"]), "2026-10-01T10:00:00+00:00"),        # Alice → Bob, negative
        (-100, 11, 1, json.dumps(["❤️"]), "2026-10-02T10:00:00+00:00"),        # Alice → Bob, positive
        (-100, 12, 2, json.dumps(["🔥", "💩"]), "2026-10-03T10:00:00+00:00"),  # Bob → Alice, sarcasm wins
        (-100, 12, 1, json.dumps(["🔥"]), "2026-10-03T10:00:00+00:00"),        # self-reaction, ignored
        (-100, 10, 3, json.dumps(["👍"]), "2026-10-03T10:00:00+00:00"),        # pair without relationship row
        (-100, 11, 1, json.dumps(["🤡"]), "2026-10-11T12:00:00+00:00"),        # already counted live
    ]
    conn.executemany("INSERT INTO chronicle_user_reactions VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    counted = repo.backfill_reaction_valence(history, updated_before="2026-10-11T09:43:14+00:00", classify=reaction_names_valence)
    assert counted == 3
    row = repo.load_relationship_states(-100)[0]
    assert (row["positive_reaction_count"], row["negative_reaction_count"]) == (1, 2)

    assert repo.backfill_reaction_valence(history, updated_before="2026-10-11T09:43:14+00:00", classify=reaction_names_valence) is None
    row = repo.load_relationship_states(-100)[0]
    assert (row["positive_reaction_count"], row["negative_reaction_count"]) == (1, 2)


def test_backfill_without_history_db_just_marks_done(tmp_path):
    repo = SQLiteSocialGraphRepository(tmp_path / "stats.db")
    repo.init_schema()
    assert repo.backfill_reaction_valence(tmp_path / "missing.db", updated_before="x", classify=reaction_names_valence) == 0
    assert repo.backfill_reaction_valence(tmp_path / "missing.db", updated_before="x", classify=reaction_names_valence) is None


# --- labelled opponents --------------------------------------------------------

def _labelled(event, pairs):
    event.ai_metadata = {"conflict_pairs": [list(pair) for pair in pairs]}
    return event


def test_thread_about_one_person_does_not_blame_everyone_who_replied_to_him(monkeypatch):
    # «Света-солнышко жалеет мудилу»: 6 человек, все отвечают пострадавшему (10),
    # а спорят на самом деле 20 и 30 против 40.  Эвристика по репликам видит
    # «бойцов» в каждой спице к 10 — включая 50, который просто спросил про сигарету.
    rows = [_row(10, 20), _row(10, 30), _row(10, 40), _row(10, 50), _row(20, 40), _row(30, 40)]
    exchanges = {"c1": {(10, 40): 4, (10, 20): 3, (10, 30): 2, (10, 50): 2, (20, 40): 1}}
    event = _event("c1", participants=(10, 20, 30, 40, 50, 60))

    _install(monkeypatch, rows, [event], exchanges=exchanges)
    guessed = _by_pair(asyncio.run(relationships.get_relationships(-100)))
    assert guessed[(10, 50)].tension > 0  # the bug: a bystander gets a «стычку»

    _install(monkeypatch, rows, [_labelled(event, [(20, 40), (30, 40)])], exchanges=exchanges)
    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))
    assert views[(20, 40)].tension > 0 and views[(20, 40)].conflict_episodes == 1
    assert views[(30, 40)].tension > 0
    for bystander in [(10, 20), (10, 30), (10, 40), (10, 50)]:
        assert views[bystander].tension == 0
        assert views[bystander].conflict_episodes == 0


def test_empty_opponent_list_means_nobody_is_blamed(monkeypatch):
    rows = [_row(1, 2)]
    event = _labelled(_event("c1", participants=(1, 2)), [])
    _install(monkeypatch, rows, [event], exchanges={"c1": {(1, 2): 9}})

    views = _by_pair(asyncio.run(relationships.get_relationships(-100)))

    assert views[(1, 2)].tension == 0
