import sqlite3
from datetime import datetime, timedelta, timezone

from infrastructure.persistence import sqlite_social_graph


class _TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


def test_social_graph_repository_closes_every_connection(monkeypatch, tmp_path):
    real_connect = sqlite3.connect
    opened = []

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = _TrackingConnection
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite_social_graph.sqlite3, "connect", tracking_connect)

    repository = sqlite_social_graph.SQLiteSocialGraphRepository(
        tmp_path / "statistics.db"
    )
    repository.init_schema()
    now = datetime.now(timezone.utc)
    actor = (1, "Alice", "alice")
    target = (2, "Bob", "bob")

    assert repository.record_message_bundle(
        -1001,
        10,
        now,
        actor,
        (actor, target),
        ((2, "reply", 3.0),),
    ) == 1
    repository.resolve_usernames(-1001, ["alice", "bob"])
    repository.resolve_message_author(-1001, 10)
    repository.load_graph(-1001, now - timedelta(days=1))
    repository.load_relationship_states(-1001)
    repository.save_relationship_snapshot(
        chat_id=-1001,
        user_a_id=1,
        user_b_id=2,
        affinity=50,
        tension=10,
        xp=1.0,
        level=1,
        reciprocity=0.5,
        archetype="test",
        trend="stable",
        captured_at=now,
    )
    repository.load_relationship_snapshots(-1001, 1, 2, 5)

    assert opened
    assert all(conn.was_closed for conn in opened)
