import asyncio
import contextlib
import json
from pathlib import Path

import pytest

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
    scores_path = tmp_path / "crocodile_scores.json"
    word_history_path = tmp_path / "crocodile_word_history.json"
    legacy_scores_path = tmp_path / "games" / "crocodile_scores.json"
    monkeypatch.setattr(persistence, "CROCODILE_STATE_PATH", state_path)
    monkeypatch.setattr(persistence, "CROCODILE_SCORES_PATH", scores_path)
    monkeypatch.setattr(persistence, "CROCODILE_WORD_HISTORY_PATH", word_history_path)
    monkeypatch.setattr(
        persistence,
        "LEGACY_CROCODILE_SCORES_PATH",
        legacy_scores_path,
    )
    monkeypatch.setattr(persistence, "CROCODILE_BUMP_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(crocodile, "BUMP_INTERVAL", 0)
    monkeypatch.setattr(crocodile, "SCORES_FILE", str(scores_path))
    monkeypatch.setattr(crocodile, "_scores", {})
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
    assert "_runtime_session_token" not in raw["sessions"][0]

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


def test_runtime_configuration_sets_one_minute_bump_and_root_score_path(
    monkeypatch,
    tmp_path,
):
    scores_path = tmp_path / "crocodile_scores.json"
    monkeypatch.setattr(persistence, "CROCODILE_SCORES_PATH", scores_path)
    monkeypatch.setattr(persistence, "CROCODILE_BUMP_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(crocodile, "BUMP_INTERVAL", 90)
    monkeypatch.setattr(crocodile, "SCORES_FILE", "old-location.json")

    persistence.configure_crocodile_runtime()

    assert crocodile.BUMP_INTERVAL == 60
    assert Path(crocodile.SCORES_FILE) == scores_path
    assert crocodile._pick_word is persistence.pick_crocodile_word


def test_score_migration_merges_root_and_misplaced_score_eras_once(
    monkeypatch,
    tmp_path,
):
    _configure_temp_state(monkeypatch, tmp_path)
    canonical = Path(persistence.CROCODILE_SCORES_PATH)
    legacy = Path(persistence.LEGACY_CROCODILE_SCORES_PATH)
    legacy.parent.mkdir(parents=True)

    canonical.write_text(
        json.dumps(
            {
                CHAT_ID: {
                    "42": {"pts": 5, "name": "Старый игрок"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    legacy.write_text(
        json.dumps(
            {
                CHAT_ID: {
                    "42": {"pts": 2, "name": "Новый ник"},
                    "77": {"pts": 3, "name": "Новый игрок"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert persistence.migrate_crocodile_scores() is True

    merged = json.loads(canonical.read_text(encoding="utf-8"))
    assert merged[CHAT_ID]["42"] == {"pts": 7, "name": "Новый ник"}
    assert merged[CHAT_ID]["77"] == {"pts": 3, "name": "Новый игрок"}
    assert crocodile._scores == merged
    assert not legacy.exists()
    archives = list(legacy.parent.glob("crocodile_scores.migrated-*.json"))
    assert len(archives) == 1

    assert persistence.migrate_crocodile_scores() is False
    assert json.loads(canonical.read_text(encoding="utf-8")) == merged


def test_word_picker_uses_every_word_before_repeating(monkeypatch, tmp_path):
    history_path = tmp_path / "crocodile_word_history.json"
    monkeypatch.setattr(persistence, "CROCODILE_WORD_HISTORY_PATH", history_path)
    monkeypatch.setattr(crocodile, "_load_words", lambda: ["Кот", "Дом", "Лес"])
    monkeypatch.setattr(persistence.random, "choice", lambda values: values[0])

    first = persistence.pick_crocodile_word()
    persisted_after_first = json.loads(history_path.read_text(encoding="utf-8"))
    assert first == "Кот"
    assert persisted_after_first["used"] == ["кот"]

    # picker перечитывает файл на каждом выборе, поэтому это же состояние
    # переживает рестарт процесса и не зависит от in-memory cache.
    remaining_cycle = [persistence.pick_crocodile_word() for _ in range(2)]
    first_cycle = [first, *remaining_cycle]
    assert first_cycle == ["Кот", "Дом", "Лес"]
    assert len(set(first_cycle)) == 3

    # Новый цикл сохраняет последние слова заблокированными, поэтому граница
    # цикла не может немедленно повторить только что выпавший ответ.
    fourth = persistence.pick_crocodile_word()
    assert fourth == "Кот"
    assert fourth != first_cycle[-1]

    payload = json.loads(history_path.read_text(encoding="utf-8"))
    assert payload["version"] == persistence.WORD_HISTORY_VERSION
    assert payload["used"] == ["дом", "лес", "кот"]


def test_reverse_crocodile_uses_shared_runtime_word_picker():
    source = (
        Path(__file__).resolve().parents[1] / "games" / "reverse_crocodile.py"
    ).read_text(encoding="utf-8")

    assert "word = crocodile_game._pick_word()" in source
    assert "random.choice(_load_words())" not in source


def test_stopping_round_closes_old_canvas_room_first(monkeypatch):
    calls = []

    class FakeSio:
        async def close_room(self, room):
            calls.append(("close", room))

    async def fake_original_stop(chat_id, reason=""):
        calls.append(("stop", str(chat_id), reason))

    monkeypatch.setattr(crocodile, "sio", FakeSio())
    monkeypatch.setattr(persistence, "_original_stop_session", fake_original_stop)

    asyncio.run(
        persistence._stop_session_and_close_canvas_room(CHAT_ID, reason="guessed")
    )

    assert calls == [
        ("close", "m1001707530786"),
        ("stop", CHAT_ID, "guessed"),
    ]


def test_round_token_rejects_canvas_from_previous_round(monkeypatch):
    socket_state = {}
    current = {"session": {}}

    class FakeSio:
        async def get_session(self, _sid):
            return dict(socket_state)

        async def save_session(self, _sid, data):
            socket_state.clear()
            socket_state.update(data)

    async def fake_original_authorize(_sid, _data, *, bind_room=False):
        return "m100", "-100", current["session"]

    monkeypatch.setattr(crocodile, "sio", FakeSio())
    monkeypatch.setattr(
        persistence,
        "_original_authorize_socket_room",
        fake_original_authorize,
    )

    asyncio.run(
        persistence._authorize_socket_room_for_current_round(
            "socket-1",
            {"room": "m100"},
            bind_room=True,
        )
    )
    old_token = socket_state["crocodile_round_token"]
    assert old_token

    # Та же вкладка и тот же пользователь могли бы пройти старую проверку по
    # chat_id/drawer_id, но новый раунд получает другой runtime token.
    current["session"] = {}
    with pytest.raises(crocodile.WebAppAuthError):
        asyncio.run(
            persistence._authorize_socket_room_for_current_round(
                "socket-1",
                {"room": "m100"},
                bind_room=False,
            )
        )
