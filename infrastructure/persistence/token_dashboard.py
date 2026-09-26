"""Read-only drill-down queries for the admin token dashboard."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TokenDashboardDrilldownRepository:
    def __init__(self, statistics_path: str | Path) -> None:
        self.statistics_path = Path(statistics_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.statistics_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def get_user_detail(
        self,
        user_id: int,
        period_hours: int | None,
        *,
        request_limit: int = 100,
    ) -> dict[str, Any]:
        params: list[Any] = [int(user_id)]
        time_sql = ""
        if period_hours is not None:
            time_sql = "AND timestamp >= ?"
            params.append(_utc_now_naive() - timedelta(hours=period_hours))

        effective_total = (
            "COALESCE(total_tokens, "
            "CASE WHEN input_tokens IS NOT NULL OR output_tokens IS NOT NULL "
            "THEN COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) ELSE 0 END)"
        )

        with closing(self._connect()) as conn:
            user = conn.execute(
                f"""
                SELECT user_id, MAX(user_name) AS user_name,
                       MAX(user_username) AS user_username,
                       COUNT(*) AS requests,
                       COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                WHERE user_id = ? {time_sql}
                GROUP BY user_id
                """,
                params,
            ).fetchone()
            if user is None:
                return {"user": None, "chats": [], "features": [], "calls": []}

            chats = conn.execute(
                f"""
                SELECT chat_id, MAX(chat_title) AS chat_title,
                       COUNT(*) AS requests,
                       COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                WHERE user_id = ? AND chat_id IS NOT NULL {time_sql}
                GROUP BY chat_id
                ORDER BY tokens DESC, requests DESC
                """,
                params,
            ).fetchall()

            features = conn.execute(
                f"""
                SELECT COALESCE(NULLIF(TRIM(feature), ''), 'не размечено') AS feature,
                       COUNT(*) AS requests,
                       COALESCE(SUM({effective_total}), 0) AS tokens
                FROM model_stats
                WHERE user_id = ? {time_sql}
                GROUP BY COALESCE(NULLIF(TRIM(feature), ''), 'не размечено')
                ORDER BY tokens DESC, requests DESC
                """,
                params,
            ).fetchall()

            calls = conn.execute(
                f"""
                SELECT timestamp, chat_id, chat_title,
                       COALESCE(NULLIF(TRIM(feature), ''), 'не размечено') AS feature,
                       COALESCE(provider, 'unknown') AS provider,
                       COALESCE(model_name, 'unknown') AS model_name,
                       COALESCE(request_type, 'unknown') AS request_type,
                       input_tokens, reasoning_tokens, output_tokens,
                       {effective_total} AS tokens,
                       success, duration_ms
                FROM model_stats
                WHERE user_id = ? {time_sql}
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                [*params, max(1, int(request_limit))],
            ).fetchall()

        return {
            "user": {
                "user_id": int(user["user_id"]),
                "user_name": user["user_name"],
                "user_username": user["user_username"],
                "requests": int(user["requests"]),
                "total_tokens": int(user["tokens"]),
            },
            "chats": [
                {
                    "chat_id": int(row["chat_id"]),
                    "chat_title": row["chat_title"],
                    "requests": int(row["requests"]),
                    "total_tokens": int(row["tokens"]),
                }
                for row in chats
            ],
            "features": [
                {
                    "feature": str(row["feature"]),
                    "requests": int(row["requests"]),
                    "total_tokens": int(row["tokens"]),
                }
                for row in features
            ],
            "calls": [
                {
                    "timestamp": row["timestamp"],
                    "chat_id": int(row["chat_id"]) if row["chat_id"] is not None else None,
                    "chat_title": row["chat_title"],
                    "feature": str(row["feature"]),
                    "provider": str(row["provider"]),
                    "model_name": str(row["model_name"]),
                    "request_type": str(row["request_type"]),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "reasoning_tokens": int(row["reasoning_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "total_tokens": int(row["tokens"] or 0),
                    "success": None if row["success"] is None else bool(row["success"]),
                    "duration_ms": int(row["duration_ms"] or 0),
                }
                for row in calls
            ],
        }
