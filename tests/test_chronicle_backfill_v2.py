from datetime import datetime, timedelta, timezone
import sqlite3

from tests import test_smoke_imports  # noqa: F401


def build_stores(tmp_path):
    from infrastructure.persistence.sqlite_chronicle_backfill import SQLiteChronicleBackfillStore
    from infrastructure.persistence.sqlite_chronicle_candidates import SQLiteChronicleCandidateStore

    path = tmp_path / "history.db"
    candidates = SQLiteChronicleCandidateStore(path)
    backfill = SQLiteChronicleBackfillStore(path)
    candidates.init_schema()
    backfill.init_schema()
    return path, candidates, backfill


def test_v1_backfill_is_reset_for_full_v2_rescan(tmp_path):
    from infrastructure.persistence.chronicle_schema import BACKFILL_STRATEGY_VERSION

    path, candidates, backfill = build_stores(tmp_path)
    chat_id = -1001
    backfill.request(chat_id)
    now = datetime.now(timezone.utc)
    candidate_id = candidates.upsert_candidate(
        chat_id=chat_id,
        candidate_key="backfill:legacy",
        timestamp=now - timedelta(days=2),
        due_at=now,
        score_floor=8.0,
        source="backfill",
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            """UPDATE chronicle_backfill_state SET
               status='running',through_history_id=777,candidates_examined=12,
               ai_requests=20,scanned_messages=777,total_messages=2000,
               queued_candidates=5,strategy_version=1 WHERE chat_id=?""",
            (chat_id,),
        )

    backfill.request(chat_id)

    state = backfill.state(chat_id)
    assert state["status"] == "pending"
    assert state["through_history_id"] == 0
    assert state["scanned_messages"] == 0
    assert state["ai_requests"] == 0
    assert state["strategy_version"] == BACKFILL_STRATEGY_VERSION
    assert candidates.get_candidate(candidate_id) is None


def test_ai_budget_no_longer_stops_local_history_scan(tmp_path):
    path, _candidates, backfill = build_stores(tmp_path)
    chat_id = -1002
    backfill.request(chat_id)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """UPDATE chronicle_backfill_state
               SET status='scanning',ai_requests=999 WHERE chat_id=?""",
            (chat_id,),
        )

    state = backfill.next(30)

    assert state is not None
    assert state["chat_id"] == chat_id
    assert state["status"] == "scanning"


def test_historical_backfill_candidates_are_not_expired_by_live_ttl(tmp_path):
    _path, candidates, _backfill = build_stores(tmp_path)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    backfill_id = candidates.upsert_candidate(
        chat_id=-10020,
        candidate_key="backfill:v2:1:2",
        timestamp=old,
        due_at=now - timedelta(minutes=1),
        score_floor=6.0,
        source="backfill",
    )
    live_id = candidates.upsert_candidate(
        chat_id=-10020,
        candidate_key="m:99",
        timestamp=old,
        due_at=now - timedelta(minutes=1),
        score_floor=6.0,
        source="live",
    )

    expired = candidates.expire(now - timedelta(hours=48))
    due_ids = {candidate.id for candidate in candidates.due_candidates(now, limit=10)}

    assert expired == 1
    assert backfill_id in due_ids
    assert live_id not in due_ids


def test_global_phrase_counts_span_multiple_scan_batches(tmp_path):
    _path, _candidates, backfill = build_stores(tmp_path)
    chat_id = -1003
    backfill.request(chat_id)
    phrase = "легендарный чайник опять"
    base_time = datetime.now(timezone.utc) - timedelta(days=10)

    for index in range(5):
        when = base_time + timedelta(hours=index)
        cluster = {
            "cluster_key": f"{index + 1}:{index + 1}",
            "history_first_id": index + 1,
            "history_last_id": index + 1,
            "started_at": when,
            "ended_at": when,
            "message_count": 1,
            "participants": [{"id": 1, "name": "Alice", "username": "alice"}],
            "source_message_ids": [100 + index],
            "anchor_message_id": 100 + index,
            "anchor_text": "легендарный чайник опять",
            "anchor_user_id": 1,
            "anchor_display_name": "Alice",
            "anchor_username": "alice",
            "reaction_count": 0,
            "base_score": 0.2,
            "phrases": [phrase],
        }
        assert backfill.record_scan_batch(
            chat_id,
            through_history_id=index + 1,
            scanned_messages=1,
            phrases={phrase: (1, when, [100 + index])},
            clusters=[cluster],
        )

    ranked = backfill.top_clusters(chat_id, limit=10, min_score=0.0)

    assert len(ranked) == 5
    assert all(item["phrase_total"] == 5 for item in ranked)
    assert all(item["final_score"] >= 3.0 for item in ranked)
    assert all(phrase in item["recurring_phrases"] for item in ranked)


def test_repeated_meme_occurrences_do_not_consume_all_ai_slots():
    from features.chronicle.backfill import _diversify_ranked

    ranked = [
        {"cluster_key": "meme-first", "recurring_phrases": ["легендарный чайник опять"]},
        {"cluster_key": "meme-second", "recurring_phrases": ["легендарный чайник опять"]},
        {"cluster_key": "other-meme", "recurring_phrases": ["боря снова обещал приехать"]},
        {"cluster_key": "strong-dialogue", "recurring_phrases": []},
    ]

    selected = _diversify_ranked(ranked, 3)

    assert [item["cluster_key"] for item in selected] == [
        "meme-first",
        "other-meme",
        "strong-dialogue",
    ]


def test_backfill_completion_cleans_only_working_index(tmp_path):
    path, _candidates, backfill = build_stores(tmp_path)
    chat_id = -1004
    backfill.request(chat_id)
    when = datetime.now(timezone.utc)
    backfill.record_scan_batch(
        chat_id,
        through_history_id=1,
        scanned_messages=1,
        phrases={"чайник снова виноват": (1, when, [1])},
        clusters=[{
            "cluster_key": "1:1",
            "history_first_id": 1,
            "history_last_id": 1,
            "started_at": when,
            "ended_at": when,
            "message_count": 1,
            "participants": [],
            "source_message_ids": [1],
            "anchor_message_id": 1,
            "anchor_text": "чайник снова виноват",
            "anchor_user_id": 1,
            "anchor_display_name": "Alice",
            "anchor_username": "alice",
            "reaction_count": 0,
            "base_score": 1.0,
            "phrases": ["чайник снова виноват"],
        }],
    )
    backfill.set_classifying(chat_id, 1)

    assert backfill.complete_if_finished(chat_id) is True
    assert backfill.state(chat_id)["status"] == "completed"
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chronicle_backfill_clusters WHERE chat_id=?", (chat_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM chronicle_backfill_phrases WHERE chat_id=?", (chat_id,)
        ).fetchone()[0] == 0


def test_chronicle_progress_reports_scan_and_classification():
    from handlers.chronicle import _backfill_progress

    scanning = _backfill_progress({
        "status": "scanning",
        "scanned_messages": 250,
        "total_messages": 1000,
    })
    classifying = _backfill_progress({
        "status": "classifying",
        "ai_requests": 7,
        "queued_candidates": 20,
    })

    assert "250 из 1000" in scanning
    assert "25%" in scanning
    assert "7/20" in classifying
