import asyncio
import contextlib
import json

from tests import test_smoke_imports  # noqa: F401
from games import crocodile
from games import crocodile_persistence as persistence


CHAT_ID = "-1001707530786"
DRAWER_ID = 424242


def _active_session(*, word="капибара", preview=b"jpeg-preview"):
    return {
        "word": word,
        "drawer_id": DRAWER_ID,
        "drawer_name": "Детектор",
        "preview_message_id": 777,
        "last_preview_time": 123.5,
        "last_preview_bytes": preview,
        "bump_task": object(),
    }


def _configure_temp_state(monkeypatch, tmp_path):
    state_path = tmp_path / "crocodile_sessions.json"
    monkeypatch.setattr(persistence, "CROCODILE_STATE_PATH", state_path)
    monkeypatch.setattr(crocodile, "BUMP_INTERVAL", 0)
    persistence._last_payload = None
    crocodile.game_sessions.clear()
    return state_path


def test_active_session_roundtrips_with_preview_and_auth_fields(monkeypatch, tmp_path):
    state_path = _configure_temp_state(monkeypatch, tmp_path)
    crocodile.game_sessions[CHAT_ID] = _active_session()

    assert persistence.persist_crocodile_sessions(force=True) is True
    assert state_path.is_file()
    assert not state_path.with_suffix(".json.tmp").exists()

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["sessions"][0]["chat_id"] == CHAT_ID
    assert raw["sessions"][0]["word"] == "капибара"
    assert "bump_task" not in raw["sessions"][0]

    crocodile.game_sessions.clear()
    persistence._last_payload = None

    assert persistence.restore_crocodile_sessions() == 1
    restored = crocodile.game_sessions[CHAT_ID]
    assert restored["word"] == "капибара"
    assert restored["drawer_id"] == DRAWER_ID
    assert restored["drawer_name"] == "Детектор"
    assert restored["preview_message_id"] == 777
    assert restored["last_preview_bytes"] == b"jpeg-preview"
    assert restored["last_preview_time"] == 0
    assert restored["bump_task"] is None


def test_restore_skips_malformed_session_records(monkeypatch, tmp_path):
    state_path = _configure_temp_state(monkeypatch, tmp_path)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [
                    {
                        "chat_id": CHAT_ID,
                        "word": "",
                        "drawer_id": DRAWER_ID,
                        "drawer_name": "Детектор",
                        "preview_message_id": 777,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert persistence.restore_crocodile_sessions() == 0
    assert crocodile.game_sessions == {}
    normalized = json.loads(state_path.read_text(encoding="utf-8"))
    assert normalized == {"sessions": [], "version": 1}


def test_persistence_loop_flushes_latest_state_when_cancelled(monkeypatch, tmp_path):
    state_path = _configure_temp_state(monkeypatch, tmp_path)
    crocodile.game_sessions[CHAT_ID] = _active_session(word="старое")

    async def scenario():
        task = asyncio.create_task(persistence.crocodile_session_persistence_loop())
        await asyncio.sleep(0)
        crocodile.game_sessions[CHAT_ID]["word"] = "новое"
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["sessions"][0]["word"] == "новое"
