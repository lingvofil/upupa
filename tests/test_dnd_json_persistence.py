import json

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
    captured = {"writes": []}

    class FakeJsonFileRepository:
        def __init__(self, path, *, ensure_ascii=False, indent=4):
            captured["path"] = path
            captured["indent"] = indent

        def save_text(self, serialized):
            captured["writes"].append(serialized)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(serialized, encoding="utf-8")

    monkeypatch.setattr(dnd, "DND_STATE_PATH", state_path)
    monkeypatch.setattr(dnd, "JsonFileRepository", FakeJsonFileRepository)
    monkeypatch.setattr(dnd, "_last_persisted", {})
    monkeypatch.setattr(
        dnd,
        "dnd_sessions",
        {
            42: _FakeSession({"chat_id": 42, "state": "WAITING_ACTION"}),
            77: _FakeSession({"chat_id": 77, "state": "LOBBY"}),
        },
    )

    dnd.persist_dnd_sessions()

    assert captured["path"] == state_path
    assert captured["indent"] is None
    assert len(captured["writes"]) == 1
    assert json.loads(captured["writes"][0]) == {
        "version": 1,
        "sessions": [
            {"chat_id": 42, "state": "WAITING_ACTION"},
            {"chat_id": 77, "state": "LOBBY"},
        ],
    }
    # Compact: no indentation whitespace.
    assert "\n" not in captured["writes"][0]


def test_identical_snapshot_is_not_rewritten_but_changes_and_deletions_are(tmp_path, monkeypatch):
    state_path = tmp_path / "dnd_state.json"
    writes = []

    class Repository:
        def __init__(self, path, **_kwargs):
            pass

        def save_text(self, serialized):
            writes.append(serialized)
            state_path.write_text(serialized, encoding="utf-8")

    session = _FakeSession({"chat_id": 1, "state": "LOBBY"})
    monkeypatch.setattr(dnd, "DND_STATE_PATH", state_path)
    monkeypatch.setattr(dnd, "JsonFileRepository", Repository)
    monkeypatch.setattr(dnd, "_last_persisted", {})
    monkeypatch.setattr(dnd, "dnd_sessions", {1: session})

    dnd.persist_dnd_sessions()
    dnd.persist_dnd_sessions()
    assert len(writes) == 1

    session._record["state"] = "WAITING_ACTION"
    dnd.persist_dnd_sessions()
    assert len(writes) == 2

    state_path.unlink()
    dnd.persist_dnd_sessions()
    assert len(writes) == 3 and state_path.exists()


def _session_with_turns(turns):
    session = dnd.GameSession(5, "Ведущий", starter_user_id=1, active_model="groq")
    for index in range(turns):
        session.conversation.append({"role": "user", "content": f"заявка {index}"})
        session.conversation.append({"role": "assistant", "content": f"сцена {index}"})
    return session


def test_record_stores_prompt_marker_and_bounded_tail_with_offset():
    session = _session_with_turns(60)
    record = session.to_record()

    assert record["conversation"][0]["content"].startswith("@@DND_SYSTEM_PROMPT@@")
    assert "Инициатор игры: Ведущий" in record["conversation"][0]["content"]
    assert dnd.DND_SYSTEM_PROMPT not in record["conversation"][0]["content"]
    assert len(record["conversation"]) == 2 + dnd.DND_PERSISTED_CONVERSATION_TAIL
    assert record["conversation"][-1]["content"] == "сцена 59"
    assert record["conversation_offset"] == 120 - dnd.DND_PERSISTED_CONVERSATION_TAIL
    # In-memory transcript is untouched.
    assert len(session.conversation) == 122


def test_restore_expands_prompt_and_keeps_absolute_sizes_for_rewind():
    session = _session_with_turns(60)
    size_before_last_turn = dnd.conversation_absolute_size(session) - 2
    restored = dnd.GameSession.from_record(json.loads(json.dumps(session.to_record(), ensure_ascii=False)))

    assert restored.conversation[0]["content"].startswith(dnd.DND_SYSTEM_PROMPT)
    assert restored.conversation[0]["content"].endswith(session.conversation[0]["content"][len(dnd.DND_SYSTEM_PROMPT):])
    assert dnd.conversation_absolute_size(restored) == dnd.conversation_absolute_size(session)

    assert dnd._rewind_session_conversation(restored, size_before_last_turn)
    assert restored.conversation[-1]["content"] == "сцена 58"
    assert dnd.conversation_absolute_size(restored) == size_before_last_turn

    # A target inside the dropped region is refused instead of wiping history.
    assert not dnd._rewind_session_conversation(restored, 10)
    assert restored.conversation[-1]["content"] == "сцена 58"


def test_legacy_record_with_full_prompt_is_migrated_on_next_persist():
    legacy_prompt = "\nТы — Мастер Подземелий (Dungeon Master) в старой версии." + "x" * 30_000
    record = {"chat_id": 9, "active_model": "groq", "state": "WAITING_ACTION", "mode": "participants",
              "conversation": [{"role": "user", "content": legacy_prompt + "\n\nИнициатор игры: Аня. Помни: не более 100 слов."},
                               {"role": "assistant", "content": "Погнали."}]}
    restored = dnd.GameSession.from_record(record)
    again = restored.to_record()
    assert again["conversation"][0]["content"] == "@@DND_SYSTEM_PROMPT@@\n\nИнициатор игры: Аня. Помни: не более 100 слов."
    assert again["conversation_offset"] == 0
