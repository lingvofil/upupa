import sqlite3

from infrastructure.persistence import sqlite_world, sqlite_world_expansion


class _TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


def test_world_expansion_repository_closes_every_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "world.db"
    world_repository = sqlite_world.SQLiteWorldRepository(db_path)
    world_repository.init_schema()
    alpha = world_repository.enable_state(-1001, "Alpha")
    beta = world_repository.enable_state(-1002, "Beta")

    real_connect = sqlite3.connect
    opened = []

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = _TrackingConnection
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite_world_expansion.sqlite3, "connect", tracking_connect)

    repository = sqlite_world_expansion.SQLiteWorldExpansionRepository(db_path)
    repository.init_schema()

    created, sanction = repository.impose_sanction(
        alpha.world_id,
        beta.world_id,
        "test reason",
    )
    assert created is True
    assert sanction is not None
    assert repository.list_active_sanctions(alpha.world_id) == [sanction]
    assert repository.lift_sanction(alpha.world_id, beta.world_id) is True
    assert repository.list_active_sanctions(alpha.world_id) == []

    case = repository.record_court_case(
        alpha.world_id,
        beta.world_id,
        "test claim",
        "test verdict",
    )
    assert repository.list_court_cases(limit=5) == [case]

    assert opened
    assert all(conn.was_closed for conn in opened)
