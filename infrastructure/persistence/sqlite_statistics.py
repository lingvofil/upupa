"""SQLite adapter for operational message/model statistics."""

from __future__ import annotations

from contextlib import closing
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SQLITE_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MS = 30_000
STATISTICS_INDEX_MIGRATION = "statistics:001-query-indexes"
MODEL_USAGE_MIGRATION = "statistics:002-model-token-usage"
AI_FEATURE_MIGRATION = "statistics:003-ai-feature"


def _utc_now_naive() -> datetime:
    """Match SQLite CURRENT_TIMESTAMP, which is stored in UTC without timezone."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SQLiteStatisticsRepository:
    """Own all SQL and connections for ``statistics.db``.

    Methods are intentionally synchronous. Async application call-sites offload
    them with ``asyncio.to_thread`` so the Telegram event loop is not blocked by
    filesystem/SQLite work.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=SQLITE_TIMEOUT_SECONDS)
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        return conn

    @staticmethod
    def _apply_feature_migration(conn: sqlite3.Connection) -> None:
        applied = conn.execute(
            "SELECT 1 FROM persistence_migrations WHERE migration_id = ?",
            (AI_FEATURE_MIGRATION,),
        ).fetchone()
        if applied:
            return

        existing_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(model_stats)").fetchall()
        }
        if "feature" not in existing_columns:
            conn.execute("ALTER TABLE model_stats ADD COLUMN feature TEXT")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_stats_feature_time "
            "ON model_stats(feature, timestamp)"
        )
        conn.execute(
            "INSERT INTO persistence_migrations(migration_id, applied_at) "
            "VALUES (?, ?)",
            (AI_FEATURE_MIGRATION, datetime.now().isoformat()),
        )

    @staticmethod
    def _apply_migrations(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persistence_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )

        index_applied = conn.execute(
            "SELECT 1 FROM persistence_migrations WHERE migration_id = ?",
            (STATISTICS_INDEX_MIGRATION,),
        ).fetchone()
        if not index_applied:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_stats_private_time "
                "ON message_stats(is_private, message_timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_stats_chat_time "
                "ON message_stats(chat_id, message_timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_stats_user_time "
                "ON message_stats(user_id, message_timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_stats_time_chat "
                "ON model_stats(timestamp, chat_id)"
            )
            conn.execute(
                "INSERT INTO persistence_migrations(migration_id, applied_at) "
                "VALUES (?, ?)",
                (STATISTICS_INDEX_MIGRATION, datetime.now().isoformat()),
            )

        usage_applied = conn.execute(
            "SELECT 1 FROM persistence_migrations WHERE migration_id = ?",
            (MODEL_USAGE_MIGRATION,),
        ).fetchone()
        if usage_applied:
            SQLiteStatisticsRepository._apply_feature_migration(conn)
            return

        existing_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(model_stats)").fetchall()
        }
        new_columns = {
            "provider": "TEXT",
            "input_tokens": "INTEGER",
            "output_tokens": "INTEGER",
            "cached_tokens": "INTEGER",
            "reasoning_tokens": "INTEGER",
            "total_tokens": "INTEGER",
            "duration_ms": "INTEGER",
            "success": "BOOLEAN",
            "lane": "TEXT",
            "chat_title": "TEXT",
            "user_name": "TEXT",
            "user_username": "TEXT",
        }
        for column_name, column_type in new_columns.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE model_stats ADD COLUMN {column_name} {column_type}"
                )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_stats_time_user "
            "ON model_stats(timestamp, user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_stats_model_time "
            "ON model_stats(model_name, timestamp)"
        )
        conn.execute(
            "INSERT INTO persistence_migrations(migration_id, applied_at) "
            "VALUES (?, ?)",
            (MODEL_USAGE_MIGRATION, datetime.now().isoformat()),
        )
        SQLiteStatisticsRepository._apply_feature_migration(conn)

    def init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    message_timestamp TIMESTAMP NOT NULL,
                    message_type TEXT NOT NULL,
                    is_private BOOLEAN NOT NULL,
                    chat_title TEXT,
                    user_name TEXT,
                    user_username TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    chat_id BIGINT,
                    user_id BIGINT,
                    model_name TEXT,
                    request_type TEXT,
                    feature TEXT,
                    provider TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cached_tokens INTEGER,
                    reasoning_tokens INTEGER,
                    total_tokens INTEGER,
                    duration_ms INTEGER,
                    success BOOLEAN,
                    lane TEXT,
                    chat_title TEXT,
                    user_name TEXT,
                    user_username TEXT
                )
                """
            )
            self._apply_migrations(conn)

    def log_model_request(
        self,
        chat_id: int | None,
        user_id: int | None,
        model_name: str,
        request_type: str,
        *,
        provider: str | None = None,
        feature: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        total_tokens: int | None = None,
        duration_ms: int | None = None,
        success: bool | None = None,
        lane: str | None = None,
        chat_title: str | None = None,
        user_name: str | None = None,
        user_username: str | None = None,
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO model_stats (
                    chat_id, user_id, model_name, request_type, feature, provider,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    total_tokens, duration_ms, success, lane, chat_title,
                    user_name, user_username
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    model_name,
                    request_type,
                    feature,
                    provider,
                    input_tokens,
                    output_tokens,
                    cached_tokens,
                    reasoning_tokens,
                    total_tokens,
                    duration_ms,
                    success,
                    lane,
                    chat_title,
                    user_name,
                    user_username,
                ),
            )

    def log_message(
        self,
        chat_id: int,
        user_id: int,
        message_type: str,
        is_private: bool,
        chat_title: str | None,
        user_name: str,
        user_username: str | None,
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO message_stats
                    (chat_id, user_id, message_timestamp, message_type, is_private,
                     chat_title, user_name, user_username)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    datetime.now(),
                    message_type,
                    is_private,
                    chat_title,
                    user_name,
                    user_username,
                ),
            )

    def get_group_chat_activity(
        self,
        active_since: datetime,
    ) -> dict[int, datetime]:
        """Return the latest group-message timestamp for recently active chats."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT chat_id, MAX(message_timestamp)
                FROM message_stats
                WHERE is_private = 0 AND message_timestamp >= ?
                GROUP BY chat_id
                """,
                (active_since,),
            ).fetchall()

        activity: dict[int, datetime] = {}
        for chat_id, timestamp in rows:
            if not timestamp:
                continue
            try:
                parsed = (
                    timestamp
                    if isinstance(timestamp, datetime)
                    else datetime.fromisoformat(str(timestamp))
                )
            except (TypeError, ValueError):
                continue
            activity[int(chat_id)] = parsed
        return activity

    def get_chat_participant_activity(
        self,
        chat_id: int,
        active_since: datetime,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recently active participant IDs and their latest known names."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT user_id, COUNT(*) AS message_count, MAX(message_timestamp) AS last_message
                FROM message_stats
                WHERE chat_id = ? AND is_private = 0 AND message_timestamp >= ?
                GROUP BY user_id
                ORDER BY message_count DESC, last_message DESC
                LIMIT ?
                """,
                (chat_id, active_since, max(1, int(limit))),
            ).fetchall()

            participants: list[dict[str, Any]] = []
            for user_id, message_count, last_message in rows:
                identity = conn.execute(
                    """
                    SELECT user_name, user_username
                    FROM message_stats
                    WHERE chat_id = ? AND user_id = ?
                    ORDER BY message_timestamp DESC
                    LIMIT 1
                    """,
                    (chat_id, user_id),
                ).fetchone()
                user_name = identity[0] if identity else None
                user_username = identity[1] if identity else None
                participants.append(
                    {
                        "user_id": int(user_id),
                        "message_count": int(message_count),
                        "last_message": str(last_message) if last_message is not None else None,
                        "user_name": user_name,
                        "user_username": user_username,
                    }
                )

        return participants

    @staticmethod
    def _last_known_user_display(conn: sqlite3.Connection, user_id: int) -> str:
        row = conn.execute(
            """
            SELECT user_name, user_username
            FROM message_stats
            WHERE user_id = ?
            ORDER BY message_timestamp DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        if not row:
            return f"User {user_id}"

        name, username = row
        if username:
            return f"{name} (@{username})" if name else f"@{username}"
        if name:
            return name
        return f"User {user_id}"

    def get_stats(self, period_hours: int | None = None) -> dict[str, dict]:
        with closing(self._connect()) as conn:
            params: list[Any] = []
            time_filter = ""
            if period_hours is not None:
                time_filter = "AND message_timestamp >= ?"
                params.append(datetime.now() - timedelta(hours=period_hours))

            group_rows = conn.execute(
                f"""
                SELECT COALESCE(chat_title, chat_id), COUNT(*)
                FROM message_stats
                WHERE is_private = 0 {time_filter}
                GROUP BY COALESCE(chat_title, chat_id)
                ORDER BY COUNT(*) DESC
                """,
                params,
            ).fetchall()
            group_stats = {str(row[0]): row[1] for row in group_rows}

            private_rows = conn.execute(
                f"""
                SELECT user_id, COUNT(*)
                FROM message_stats
                WHERE is_private = 1 {time_filter}
                GROUP BY user_id
                ORDER BY COUNT(*) DESC
                """,
                params,
            ).fetchall()
            private_stats = {
                self._last_known_user_display(conn, user_id): count
                for user_id, count in private_rows
            }

            model_params: list[Any] = []
            model_time_filter = ""
            if period_hours:
                model_time_filter = "WHERE timestamp >= ?"
                model_params.append(_utc_now_naive() - timedelta(hours=period_hours))

            model_rows = conn.execute(
                f"""
                SELECT chat_id, COUNT(*)
                FROM model_stats
                {model_time_filter}
                GROUP BY chat_id
                ORDER BY COUNT(*) DESC
                """,
                model_params,
            ).fetchall()

            model_usage: dict[str, int] = {}
            for chat_id, count in model_rows:
                if not chat_id:
                    key = "Неизвестный чат / API"
                else:
                    row = conn.execute(
                        "SELECT chat_title FROM message_stats WHERE chat_id = ? LIMIT 1",
                        (chat_id,),
                    ).fetchone()
                    key = row[0] if row and row[0] else f"ID: {chat_id}"
                model_usage[key] = count

        return {
            "groups": group_stats,
            "private": private_stats,
            "model_usage": model_usage,
        }

    def get_model_usage_report(
        self,
        period_hours: int | None = 24,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Aggregate real provider token usage by model, chat and user."""
        resolved_limit = max(1, int(limit))
        params: list[Any] = []
        where = ""
        if period_hours is not None:
            where = "WHERE timestamp >= ?"
            params.append(_utc_now_naive() - timedelta(hours=period_hours))

        usage_known = (
            "input_tokens IS NOT NULL "
            "OR output_tokens IS NOT NULL "
            "OR total_tokens IS NOT NULL"
        )
        effective_total = (
            "COALESCE(total_tokens, "
            "CASE WHEN input_tokens IS NOT NULL OR output_tokens IS NOT NULL "
            "THEN COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) END)"
        )

        with closing(self._connect()) as conn:
            totals_row = conn.execute(
                f"""
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN input_tokens IS NOT NULL
                              OR output_tokens IS NOT NULL
                              OR total_tokens IS NOT NULL THEN 1 ELSE 0 END),
                    COALESCE(SUM(input_tokens), 0),
                    COALESCE(SUM(output_tokens), 0),
                    COALESCE(SUM(cached_tokens), 0),
                    COALESCE(SUM(reasoning_tokens), 0),
                    COALESCE(SUM({effective_total}), 0),
                    SUM(CASE WHEN chat_id IS NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN user_id IS NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN lane = 'interactive' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN lane = 'background' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN success IS NULL THEN 1 ELSE 0 END)
                FROM model_stats
                {where}
                """,
                params,
            ).fetchone() or (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

            telemetry_started_row = conn.execute(
                """
                SELECT MIN(timestamp)
                FROM model_stats
                WHERE input_tokens IS NOT NULL
                   OR output_tokens IS NOT NULL
                   OR total_tokens IS NOT NULL
                """
            ).fetchone()

            models = conn.execute(
                f"""
                SELECT
                    COALESCE(provider, 'unknown') AS provider_name,
                    COALESCE(model_name, 'unknown') AS resolved_model,
                    COUNT(*) AS requests,
                    SUM(CASE WHEN {usage_known} THEN 1 ELSE 0 END) AS usage_known_requests,
                    COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                {where}
                GROUP BY provider_name, resolved_model
                ORDER BY tokens DESC, requests DESC
                LIMIT ?
                """,
                [*params, resolved_limit],
            ).fetchall()

            request_types = conn.execute(
                f"""
                SELECT
                    COALESCE(request_type, 'unknown') AS resolved_request_type,
                    COUNT(*) AS requests,
                    SUM(CASE WHEN {usage_known} THEN 1 ELSE 0 END) AS usage_known_requests,
                    COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                {where}
                GROUP BY resolved_request_type
                ORDER BY tokens DESC, requests DESC
                LIMIT ?
                """,
                [*params, resolved_limit],
            ).fetchall()

            scoped_where = f"{where} {'AND' if where else 'WHERE'} chat_id IS NOT NULL"
            chats = conn.execute(
                f"""
                SELECT
                    chat_id,
                    MAX(chat_title),
                    COUNT(*) AS requests,
                    COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                {scoped_where}
                GROUP BY chat_id
                ORDER BY tokens DESC, requests DESC
                LIMIT ?
                """,
                [*params, resolved_limit],
            ).fetchall()

            user_where = f"{where} {'AND' if where else 'WHERE'} user_id IS NOT NULL"
            users = conn.execute(
                f"""
                SELECT
                    user_id,
                    MAX(user_name),
                    MAX(user_username),
                    COUNT(*) AS requests,
                    COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                {user_where}
                GROUP BY user_id
                ORDER BY tokens DESC, requests DESC
                LIMIT ?
                """,
                [*params, resolved_limit],
            ).fetchall()

            users_by_requests = conn.execute(
                f"""
                SELECT
                    user_id,
                    MAX(user_name),
                    MAX(user_username),
                    COUNT(*) AS requests,
                    COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                {user_where}
                GROUP BY user_id
                ORDER BY requests DESC, tokens DESC
                LIMIT ?
                """,
                [*params, resolved_limit],
            ).fetchall()

        return {
            "totals": {
                "requests": int(totals_row[0] or 0),
                "successful_requests": int(totals_row[1] or 0),
                "usage_known_requests": int(totals_row[2] or 0),
                "input_tokens": int(totals_row[3] or 0),
                "output_tokens": int(totals_row[4] or 0),
                "cached_tokens": int(totals_row[5] or 0),
                "reasoning_tokens": int(totals_row[6] or 0),
                "total_tokens": int(totals_row[7] or 0),
                "unattributed_requests": int(totals_row[8] or 0),
                "unattributed_chat_requests": int(totals_row[8] or 0),
                "unattributed_user_requests": int(totals_row[9] or 0),
                "interactive_requests": int(totals_row[10] or 0),
                "background_requests": int(totals_row[11] or 0),
                "failed_requests": int(totals_row[12] or 0),
                "unknown_outcome_requests": int(totals_row[13] or 0),
                "telemetry_started_at": (
                    telemetry_started_row[0]
                    if telemetry_started_row and telemetry_started_row[0]
                    else None
                ),
            },
            "models": [
                {
                    "provider": str(provider),
                    "model_name": str(model_name),
                    "requests": int(requests),
                    "usage_known_requests": int(usage_known_requests or 0),
                    "total_tokens": int(tokens),
                    "average_tokens": (
                        round(int(tokens) / int(usage_known_requests))
                        if usage_known_requests
                        else 0
                    ),
                }
                for provider, model_name, requests, usage_known_requests, tokens in models
            ],
            "request_types": [
                {
                    "request_type": str(request_type),
                    "requests": int(requests),
                    "usage_known_requests": int(usage_known_requests or 0),
                    "total_tokens": int(tokens),
                    "average_tokens": (
                        round(int(tokens) / int(usage_known_requests))
                        if usage_known_requests
                        else 0
                    ),
                }
                for request_type, requests, usage_known_requests, tokens in request_types
            ],
            "chats": [
                {
                    "chat_id": int(chat_id),
                    "chat_title": chat_title,
                    "requests": int(requests),
                    "total_tokens": int(tokens),
                }
                for chat_id, chat_title, requests, tokens in chats
            ],
            "users": [
                {
                    "user_id": int(user_id),
                    "user_name": user_name,
                    "user_username": user_username,
                    "requests": int(requests),
                    "total_tokens": int(tokens),
                }
                for user_id, user_name, user_username, requests, tokens in users
            ],
            "users_by_requests": [
                {
                    "user_id": int(user_id),
                    "user_name": user_name,
                    "user_username": user_username,
                    "requests": int(requests),
                    "total_tokens": int(tokens),
                }
                for user_id, user_name, user_username, requests, tokens in users_by_requests
            ],
        }

    def get_activity_by_hour(self, period_hours: int | None = None) -> dict[int, int]:
        params: list[Any] = []
        time_filter = ""
        if period_hours is not None:
            time_filter = "WHERE message_timestamp >= ?"
            params.append(datetime.now() - timedelta(hours=period_hours))

        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT strftime('%H', message_timestamp), COUNT(*)
                FROM message_stats
                {time_filter}
                GROUP BY strftime('%H', message_timestamp)
                """,
                params,
            ).fetchall()

        data = {hour: 0 for hour in range(24)}
        for hour, count in rows:
            data[int(hour)] = count
        return data
