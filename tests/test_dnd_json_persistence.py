from tests import test_smoke_imports

del test_smoke_imports

import AI.dnd as dnd


class _FakeSession:
    def __init__(self, record):
        self._record = record

    def to_record(self):
        return dict(self._record)


def test_persist_dnd_sessions_uses_json_file_repository(tmp_path, monkeypatch):
    state_path = tmp_path / "nested" / "dnd_state.json"
    captured = {}

    class FakeJsonFileRepository:
        def __init__(self, path, *, ensure_ascii=False, indent=4):
            captured["path"] = path
            captured["ensure_ascii"] = ensure_ascii
            captured["indent"] = indent

        def save(self, value):
            captured["value"] = value

    monkeypatch.setattr(dnd, "DND_STATE_PATH", state_path)
    monkeypatch.setattr(dnd, "JsonFileRepository", FakeJsonFileRepository)
    monkeypatch.setattr(
        dnd,
        "dnd_sessions",
        {
            42: _FakeSession({"chat_id": 42, "state": "WAITING_ACTION"}),
            77: _FakeSession({"chat_id": 77, "state": "LOBBY"}),
        },
    )

    dnd.persist_dnd_sessions()

    assert captured == {
        "path": state_path,
        "ensure_ascii": False,
        "indent": 2,
        "value": {
            "version": 1,
            "sessions": [
                {"chat_id": 42, "state": "WAITING_ACTION"},
                {"chat_id": 77, "state": "LOBBY"},
            ],
        },
    }
