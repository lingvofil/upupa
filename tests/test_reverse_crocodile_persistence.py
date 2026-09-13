import time
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


@pytest.mark.parametrize(
    "mode",
    ["word", "reveal", "movie", "cartoon", "proverbs", "pun"],
)
def test_reverse_crocodile_sessions_survive_restart_for_every_mode(
    mode,
    tmp_path,
    monkeypatch,
):
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_modes as modes
    from games import reverse_crocodile_persistence as persistence

    state_path = tmp_path / "reverse_crocodile_state.json"
    monkeypatch.setattr(
        persistence.base_persistence,
        "CROCODILE_STATE_PATH",
        tmp_path / "crocodile_sessions.json",
    )
    monkeypatch.setattr(persistence, "_last_payload", None)
    monkeypatch.setattr(persistence, "_restored", False)

    started_tasks = []

    def fake_start(coro, *, name):
        started_tasks.append(name)
        coro.close()
        return SimpleNamespace(name=name)

    monkeypatch.setattr(persistence.crocodile, "_start_background_task", fake_start)

    chat_id = "-1001234567890"
    now = time.monotonic()
    word = (
        "Без труда не вытащишь и рыбку из пруда"
        if mode == "proverbs"
        else "лабиринт"
    )
    session = {
        "word": word,
        "difficulty": "hard",
        "image": b"persisted-image-bytes",
        "message_id": 4242,
        "started_at": now - 123,
        "mode_task": None,
        "round_task": None,
        "hints": 2,
        "revealed_positions": {1, 4},
        "hint_lock": None,
        "last_hint_at": now - 17,
        "revealed_tiles": 9,
        "reveal_order": list(range(modes.REVEAL_COLS * modes.REVEAL_ROWS)),
    }
    if mode != "word":
        session["mode"] = mode

    reverse.games.clear()
    reverse.games[chat_id] = session
    try:
        assert persistence.persist_reverse_crocodile_sessions(force=True) is True
        assert state_path.is_file()

        reverse.games.clear()
        assert persistence.restore_reverse_crocodile_sessions() == 1

        restored = reverse.games[chat_id]
        assert restored["word"] == session["word"]
        assert restored["difficulty"] == "hard"
        assert restored["mode"] == mode
        assert restored["image"] == b"persisted-image-bytes"
        assert restored["message_id"] == 4242
        assert restored["hints"] == 2
        assert restored["revealed_positions"] == {1, 4}
        assert restored["revealed_tiles"] == 9
        assert restored["reveal_order"] == session["reveal_order"]
        assert restored["hint_lock"] is not None

        elapsed = time.monotonic() - restored["started_at"]
        assert elapsed == pytest.approx(123, abs=1.0)
        hint_elapsed = time.monotonic() - restored["last_hint_at"]
        assert hint_elapsed == pytest.approx(17, abs=1.0)

        assert any(name.endswith(":restored") for name in started_tasks)
        if mode == "reveal":
            assert len(started_tasks) == 2
            assert any("reveal" in name for name in started_tasks)
        else:
            assert len(started_tasks) == 1
    finally:
        reverse.games.clear()


def test_reverse_timer_payload_stays_stable_while_countdowns_run(monkeypatch):
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_persistence as persistence

    chat_id = "-77"
    session = {
        "word": "ревность",
        "difficulty": "medium",
        "image": b"image",
        "message_id": 12,
        "started_at": 900.0,
        "hints": 1,
        "revealed_positions": set(),
        "last_hint_at": 980.0,
    }
    reverse.games.clear()
    reverse.games[chat_id] = session
    try:
        monkeypatch.setattr(persistence.time, "monotonic", lambda: 1000.0)
        monkeypatch.setattr(persistence.time, "time", lambda: 5000.0)
        first = persistence._serialize_current_state()

        monkeypatch.setattr(persistence.time, "monotonic", lambda: 1030.0)
        monkeypatch.setattr(persistence.time, "time", lambda: 5030.0)
        second = persistence._serialize_current_state()

        assert second == first
    finally:
        reverse.games.clear()


def test_reverse_persistence_skips_unpublished_round(tmp_path, monkeypatch):
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_persistence as persistence

    state_path = tmp_path / "reverse_crocodile_state.json"
    monkeypatch.setattr(
        persistence.base_persistence,
        "CROCODILE_STATE_PATH",
        tmp_path / "crocodile_sessions.json",
    )
    monkeypatch.setattr(persistence, "_last_payload", None)

    reverse.games.clear()
    reverse.games["-42"] = {
        "word": "черновик",
        "difficulty": "medium",
        "image": b"image",
        "message_id": None,
        "started_at": time.monotonic(),
    }
    try:
        persistence.persist_reverse_crocodile_sessions(force=True)
        payload = state_path.read_text(encoding="utf-8")
        assert '"sessions": []' in payload
    finally:
        reverse.games.clear()


def test_extra_persistence_composition_runs_every_callback():
    from games import crocodile_runtime as runtime

    persisted = []
    restored = []

    def first_persist(*, force=False):
        persisted.append(("first", force))
        return True

    def second_persist(*, force=False):
        persisted.append(("second", force))
        return False

    def first_restore():
        restored.append("first")
        return 2

    def second_restore():
        restored.append("second")
        return 3

    persist = runtime._compose_extra_persistors(first_persist, second_persist)
    restore = runtime._compose_extra_restorers(first_restore, second_restore)

    assert persist(force=True) is True
    assert persisted == [("first", True), ("second", True)]
    assert restore() == 5
    assert restored == ["first", "second"]
