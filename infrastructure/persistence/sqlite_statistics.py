"""SQLite adapter for operational message/model statistics."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


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
        return sqlite3.connect(self.path)

    def init_schema(self) -> None:
        with self._connect() as conn:
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
                    request_type TEXT
                )
                """
            )

    def log_model_request(
        self,
        chat_id: int | None,
        user_id: int | None,
        model_name: str,
        request_type: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_stats (chat_id, user_id, model_name, request_type)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, user_id, model_name, request_type),
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
                model_params.append(datetime.now() - timedelta(hours=period_hours))

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

    def get_activity_by_hour(self, period_hours: int | None = None) -> dict[int, int]:
        params: list[Any] = []
        time_filter = ""
        if period_hours is not None:
            time_filter = "WHERE message_timestamp >= ?"
            params.append(datetime.now() - timedelta(hours=period_hours))

        with self._connect() as conn:
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
