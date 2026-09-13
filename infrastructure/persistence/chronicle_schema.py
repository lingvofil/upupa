"""Schema helpers for Chronicle tables stored in history.db."""

from __future__ import annotations

import sqlite3


BACKFILL_STRATEGY_VERSION = 2


SCHEMA = """
CREATE TABLE IF NOT EXISTS chronicle_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    candidate_key TEXT NOT NULL,
    anchor_message_id INTEGER,
    started_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    anchor_text TEXT NOT NULL DEFAULT '',
    anchor_user_id INTEGER,
    anchor_display_name TEXT NOT NULL DEFAULT '',
    anchor_username TEXT,
    score REAL NOT NULL DEFAULT 0,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    unique_reactors INTEGER NOT NULL DEFAULT 0,
    reply_count INTEGER NOT NULL DEFAULT 0,
    participants_json TEXT NOT NULL DEFAULT '[]',
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'live',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'observing',
    reject_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, candidate_key)
);
CREATE INDEX IF NOT EXISTS idx_chronicle_candidates_due
    ON chronicle_candidates(status, due_at);

CREATE TABLE IF NOT EXISTS chronicle_user_reactions (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    reactions_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(chat_id, message_id, user_id)
);
CREATE TABLE IF NOT EXISTS chronicle_reaction_aggregate (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    type_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS chronicle_events (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    event_started_at TEXT NOT NULL,
    event_ended_at TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    category TEXT NOT NULL,
    importance_score REAL NOT NULL,
    anchor_message_id INTEGER,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    unique_reactors INTEGER NOT NULL DEFAULT 0,
    reply_count INTEGER NOT NULL DEFAULT 0,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    entities_json TEXT NOT NULL DEFAULT '[]',
    related_event_ids_json TEXT NOT NULL DEFAULT '[]',
    ai_metadata_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS idx_chronicle_events_chat_time
    ON chronicle_events(chat_id, event_started_at DESC);

CREATE TABLE IF NOT EXISTS chronicle_event_participants (
    event_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    username TEXT,
    PRIMARY KEY(event_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_chronicle_event_participant_user
    ON chronicle_event_participants(user_id, event_id);
CREATE INDEX IF NOT EXISTS idx_chronicle_event_participant_username
    ON chronicle_event_participants(username COLLATE NOCASE, event_id);

CREATE TABLE IF NOT EXISTS chronicle_event_sources (
    event_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY(event_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_chronicle_event_source_message
    ON chronicle_event_sources(message_id, event_id);

CREATE TABLE IF NOT EXISTS chronicle_backfill_state (
    chat_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    through_history_id INTEGER NOT NULL DEFAULT 0,
    candidates_examined INTEGER NOT NULL DEFAULT 0,
    ai_requests INTEGER NOT NULL DEFAULT 0,
    requested_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    scanned_messages INTEGER NOT NULL DEFAULT 0,
    total_messages INTEGER NOT NULL DEFAULT 0,
    queued_candidates INTEGER NOT NULL DEFAULT 0,
    strategy_version INTEGER NOT NULL DEFAULT 2
);
CREATE TABLE IF NOT EXISTS chronicle_backfill_phrases (
    chat_id INTEGER NOT NULL,
    phrase TEXT NOT NULL,
    occurrences INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    sample_message_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(chat_id, phrase)
);
CREATE INDEX IF NOT EXISTS idx_chronicle_phrase_frequency
    ON chronicle_backfill_phrases(chat_id, occurrences DESC);

CREATE TABLE IF NOT EXISTS chronicle_backfill_clusters (
    chat_id INTEGER NOT NULL,
    cluster_key TEXT NOT NULL,
    history_first_id INTEGER NOT NULL,
    history_last_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    participants_json TEXT NOT NULL DEFAULT '[]',
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    anchor_message_id INTEGER,
    anchor_text TEXT NOT NULL DEFAULT '',
    anchor_user_id INTEGER,
    anchor_display_name TEXT NOT NULL DEFAULT '',
    anchor_username TEXT,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    base_score REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY(chat_id, cluster_key)
);
CREATE INDEX IF NOT EXISTS idx_chronicle_backfill_cluster_score
    ON chronicle_backfill_clusters(chat_id, base_score DESC);

CREATE TABLE IF NOT EXISTS chronicle_backfill_cluster_phrases (
    chat_id INTEGER NOT NULL,
    cluster_key TEXT NOT NULL,
    phrase TEXT NOT NULL,
    PRIMARY KEY(chat_id, cluster_key, phrase)
);
CREATE INDEX IF NOT EXISTS idx_chronicle_backfill_cluster_phrase
    ON chronicle_backfill_cluster_phrases(chat_id, phrase);

CREATE TABLE IF NOT EXISTS chronicle_metrics (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_chronicle_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Production may already have the v1 Chronicle tables. Keep the migration
    # additive so rolling forward does not destroy already saved events.
    _ensure_column(conn, "chronicle_backfill_state", "scanned_messages", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "chronicle_backfill_state", "total_messages", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "chronicle_backfill_state", "queued_candidates", "INTEGER NOT NULL DEFAULT 0")
    # Existing rows came from strategy v1, so the additive migration must mark
    # them as v1. Fresh databases get DEFAULT 2 from SCHEMA above.
    _ensure_column(conn, "chronicle_backfill_state", "strategy_version", "INTEGER NOT NULL DEFAULT 1")
