import sqlite3

from infrastructure.persistence import sqlite_world


class _TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


def test_world_repository_closes_every_connection(monkeypatch, tmp_path):
    real_connect = sqlite3.connect
    opened = []

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = _TrackingConnection
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite_world.sqlite3, "connect", tracking_connect)

    repository = sqlite_world.SQLiteWorldRepository(tmp_path / "world.db")
    repository.init_schema()
    alpha = repository.enable_state(-1001, "Alpha")
    beta = repository.enable_state(-1002, "Beta")

    assert repository.get_state_by_chat_id(-1001) == alpha
    assert repository.get_state_by_world_id(beta.world_id) == beta
    assert len(repository.list_enabled_states()) == 2

    status, request = repository.create_request(alpha.world_id, beta.world_id)
    assert status == "created"
    assert request is not None
    assert repository.get_pending_request_between(alpha.world_id, beta.world_id) == request
    assert repository.cancel_request(request.request_id)
    assert repository.get_relation(alpha.world_id, beta.world_id) is None
    assert repository.disable_state(-1002) is not None

    assert opened
    assert all(conn.was_closed for conn in opened)
