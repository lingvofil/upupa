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
    repository.get_stats(period_hours=1)
    repository.get_activity_by_hour(period_hours=1)

    assert opened
    assert all(conn.was_closed for conn in opened)
