import sqlite3
from datetime import datetime, timedelta

from infrastructure.persistence import sqlite_statistics


class _TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


def test_statistics_repository_closes_every_connection(monkeypatch, tmp_path):
    real_connect = sqlite3.connect
    opened = []

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = _TrackingConnection
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite_statistics.sqlite3, "connect", tracking_connect)

    repository = sqlite_statistics.SQLiteStatisticsRepository(
        tmp_path / "statistics.db"
    )
    repository.init_schema()
    repository.log_message(
        chat_id=-1001,
        user_id=42,
        message_type="text",
        is_private=False,
        chat_title="Test chat",
        user_name="Tester",
        user_username="tester",
    )
    repository.log_model_request(
        chat_id=-1001,
        user_id=42,
        model_name="test-model",
        request_type="dialog",
    )
    repository.get_group_chat_activity(datetime.now() - timedelta(hours=1))
    repository.get_chat_participant_activity(
        -1001,
        datetime.now() - timedelta(hours=1),
        limit=10,
    )
    repository.get_stats(period_hours=1)
    repository.get_model_usage_report(period_hours=1)
    repository.get_activity_by_hour(period_hours=1)

    assert opened
    assert all(conn.was_closed for conn in opened)


def test_recent_chat_participant_activity_is_ranked_and_scoped(tmp_path):
    repository = sqlite_statistics.SQLiteStatisticsRepository(tmp_path / "statistics.db")
    repository.init_schema()

    for _ in range(3):
        repository.log_message(
            chat_id=-1001,
            user_id=66,
            message_type="text",
            is_private=False,
            chat_title="Test chat",
            user_name="Карл",
            user_username="karl_bot",
        )
    repository.log_message(
        chat_id=-1001,
        user_id=77,
        message_type="text",
        is_private=False,
        chat_title="Test chat",
        user_name="Сглыпа",
        user_username="sglypa_bot",
    )
    repository.log_message(
        chat_id=-2002,
        user_id=88,
        message_type="text",
        is_private=False,
        chat_title="Other chat",
        user_name="Чужой",
        user_username=None,
    )

    rows = repository.get_chat_participant_activity(
        -1001,
        datetime.now() - timedelta(hours=1),
        limit=10,
    )

    assert [row["user_id"] for row in rows] == [66, 77]
    assert rows[0]["message_count"] == 3
    assert rows[0]["user_name"] == "Карл"
    assert rows[0]["user_username"] == "karl_bot"



def test_model_usage_report_aggregates_tokens_by_model_chat_and_user(tmp_path):
    repository = sqlite_statistics.SQLiteStatisticsRepository(tmp_path / "statistics.db")
    repository.init_schema()

    repository.log_model_request(
        chat_id=-1001,
        user_id=42,
        model_name="gemini-test",
        request_type="model.generate_content",
        provider="gemini",
        input_tokens=100,
        output_tokens=25,
        total_tokens=130,
        cached_tokens=10,
        reasoning_tokens=5,
        duration_ms=900,
        success=True,
        lane="interactive",
        chat_title="Heavy chat",
        user_name="Alice",
        user_username="alice",
    )
    repository.log_model_request(
        chat_id=-1001,
        user_id=42,
        model_name="gemini-test",
        request_type="model.generate_content",
        provider="gemini",
        input_tokens=50,
        output_tokens=20,
        total_tokens=75,
        duration_ms=500,
        success=True,
        lane="interactive",
        chat_title="Heavy chat",
        user_name="Alice",
        user_username="alice",
    )
    repository.log_model_request(
        chat_id=-2002,
        user_id=77,
        model_name="deepseek-test",
        request_type="siliconflow_ai.generate_text",
        provider="siliconflow",
        input_tokens=40,
        output_tokens=10,
        total_tokens=50,
        duration_ms=400,
        success=True,
        lane="interactive",
        chat_title="Light chat",
        user_name="Bob",
        user_username=None,
    )
    repository.log_model_request(
        chat_id=None,
        user_id=None,
        model_name="unknown",
        request_type="background.task",
        provider="unknown",
        duration_ms=100,
        success=False,
        lane="background",
    )

    report = repository.get_model_usage_report(period_hours=1, limit=5)
    totals = report["totals"]

    assert totals["requests"] == 4
    assert totals["successful_requests"] == 3
    assert totals["usage_known_requests"] == 3
    assert totals["input_tokens"] == 190
    assert totals["output_tokens"] == 55
    assert totals["cached_tokens"] == 10
    assert totals["reasoning_tokens"] == 5
    assert totals["total_tokens"] == 255
    assert totals["unattributed_requests"] == 1

    assert report["models"][0]["model_name"] == "gemini-test"
    assert report["models"][0]["total_tokens"] == 205
    assert report["chats"][0]["chat_id"] == -1001
    assert report["chats"][0]["chat_title"] == "Heavy chat"
    assert report["chats"][0]["total_tokens"] == 205
    assert report["users"][0]["user_id"] == 42
    assert report["users"][0]["user_username"] == "alice"
    assert report["users"][0]["total_tokens"] == 205


def test_statistics_schema_migrates_legacy_model_stats_table(tmp_path):
    path = tmp_path / "statistics.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE message_stats (
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
            CREATE TABLE model_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                chat_id BIGINT,
                user_id BIGINT,
                model_name TEXT,
                request_type TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE persistence_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO persistence_migrations(migration_id, applied_at) VALUES (?, ?)",
            (sqlite_statistics.STATISTICS_INDEX_MIGRATION, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    repository = sqlite_statistics.SQLiteStatisticsRepository(path)
    repository.init_schema()

    conn = sqlite3.connect(path)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(model_stats)").fetchall()
        }
        migrations = {
            row[0]
            for row in conn.execute(
                "SELECT migration_id FROM persistence_migrations"
            ).fetchall()
        }
    finally:
        conn.close()

    assert {
        "provider",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "total_tokens",
        "duration_ms",
        "success",
        "lane",
        "chat_title",
        "user_name",
        "user_username",
    } <= columns
    assert sqlite_statistics.MODEL_USAGE_MIGRATION in migrations
