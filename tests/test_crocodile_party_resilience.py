import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _message(chat_id: int, user_id: int, name: str = "Игрок", text: str = "кот"):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id, full_name=name),
        text=text,
    )


def test_duo_persistence_roundtrip_keeps_both_drawers():
    from games import crocodile_party_state as state

    dependencies = state.crocodile_persistence_dependencies()
    assert dependencies.enrich_session_record is not None
    assert dependencies.enrich_restored_session is not None

    session = {
        "word": "кот",
        "drawer_id": 11,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [11, 22],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
    }
    base_record = {
        "chat_id": "-42",
        "word": session["word"],
        "drawer_id": session["drawer_id"],
    }

    record = dependencies.enrich_session_record("-42", session, base_record)
    chat_id, restored = dependencies.enrich_restored_session(
        record,
        "-42",
        {"word": record["word"], "drawer_id": record["drawer_id"]},
    )

    assert chat_id == "-42"
    assert restored["drawer_ids"] == [11, 22]
    assert restored["drawer_names"] == ["Первый", "Второй"]
    assert restored["mode"] == "duo"


def test_persistence_dependency_flushes_extra_state_when_regular_state_is_unchanged(
    monkeypatch,
):
    from games import crocodile_persistence as persistence

    persist_extra = MagicMock(return_value=True)
    dependencies = persistence.CrocodilePersistenceDependencies(
        persist_extra_state=persist_extra
    )
    monkeypatch.setattr(persistence, "_persistence_dependencies", dependencies)
    monkeypatch.setattr(persistence, "_serialize_current_state", lambda: "{}")
    monkeypatch.setattr(persistence, "_last_payload", "{}")

    assert persistence.persist_crocodile_sessions() is True
    persist_extra.assert_called_once_with(force=False)


def test_persistence_dependency_restores_extra_state_before_regular_file_check(
    tmp_path,
    monkeypatch,
):
    from games import crocodile_persistence as persistence

    restore_extra = MagicMock(return_value=2)
    dependencies = persistence.CrocodilePersistenceDependencies(
        restore_extra_state=restore_extra
    )
    monkeypatch.setattr(persistence, "_persistence_dependencies", dependencies)
    monkeypatch.setattr(persistence, "configure_crocodile_runtime", MagicMock())
    monkeypatch.setattr(persistence, "migrate_crocodile_scores", MagicMock())
    monkeypatch.setattr(persistence, "_state_path", lambda: tmp_path / "missing.json")

    assert persistence.restore_crocodile_sessions() == 0
    restore_extra.assert_called_once_with()


def test_duel_vote_deadline_is_set_once_by_modes(monkeypatch):
    from games import crocodile, crocodile_modes

    duel = {"phase": "answer_resolved", "votes": {}}
    created_tasks = []

    def start_background_task(coro, *, name):
        coro.close()
        created_tasks.append(name)
        return object()

    monkeypatch.setattr(crocodile_modes.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        crocodile_modes,
        "bot",
        SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock()),
    )
    monkeypatch.setattr(crocodile, "_start_background_task", start_background_task)
    crocodile_modes.canvas_sessions.clear()

    asyncio.run(crocodile_modes._start_duel_vote("-42", duel))

    assert duel["phase"] == "voting"
    assert duel["vote_deadline"] == 1000.0 + crocodile_modes.DUEL_VOTE_SECONDS
    first_task = duel["vote_task"]
    assert created_tasks == ["crocodile-duel-vote:-42"]

    asyncio.run(crocodile_modes._start_duel_vote("-42", duel))

    assert duel["vote_deadline"] == 1000.0 + crocodile_modes.DUEL_VOTE_SECONDS
    assert duel["vote_task"] is first_task
    assert created_tasks == ["crocodile-duel-vote:-42"]


def test_party_state_roundtrip_restores_duel_phone_and_canvases(tmp_path, monkeypatch):
    from games import crocodile_modes
    from games import crocodile_party_state as state

    monkeypatch.setattr(state, "PARTY_STATE_PATH", tmp_path / "party.json")
    monkeypatch.setattr(state, "_restored", False)
    monkeypatch.setattr(state, "_last_payload", None)
    crocodile_modes.duel_games.clear()
    crocodile_modes.telephone_games.clear()
    crocodile_modes.canvas_sessions.clear()
    crocodile_modes.duel_games["-42"] = {
        "phase": "drawing",
        "host_id": 1,
        "artists": [(1, "Первый"), (2, "Второй")],
        "votes": {},
        "word": "барсук",
        "started_at": 123.0,
        "finished": {1},
    }
    crocodile_modes.telephone_games["-43"] = {
        "phase": "playing",
        "host_id": 3,
        "players": [(3, "А"), (4, "Б"), (5, "В")],
        "chain": [{"kind": "text", "value": "слово", "user_id": 3, "user_name": "А"}],
        "step": 1,
    }
    crocodile_modes.canvas_sessions["-42:d2"] = {
        "chat_id": "-42",
        "drawer_id": 2,
        "drawer_name": "Второй",
        "word": "барсук",
        "mode": "duel",
        "slot": 2,
        "ui_mode": "draw",
        "last_preview_bytes": b"duel-image",
        "duel_chat_id": "-42",
    }
    crocodile_modes.canvas_sessions["-43:t1"] = {
        "chat_id": "-43",
        "drawer_id": 4,
        "drawer_name": "Б",
        "word": "слово",
        "mode": "telephone",
        "telephone_step": 1,
        "ui_mode": "draw",
        "last_preview_bytes": b"phone-image",
        "telephone_chat_id": "-43",
        "suppress_chat_preview": True,
    }

    try:
        assert state.persist_party_modes(force=True)
        crocodile_modes.duel_games.clear()
        crocodile_modes.telephone_games.clear()
        crocodile_modes.canvas_sessions.clear()

        assert state.restore_party_modes() == 2
        assert crocodile_modes.duel_games["-42"]["artists"] == [(1, "Первый"), (2, "Второй")]
        assert crocodile_modes.duel_games["-42"]["finished"] == {1}
        assert crocodile_modes.telephone_games["-43"]["step"] == 1
        assert crocodile_modes.telephone_games["-43"]["chain"][0]["value"] == "слово"
        assert crocodile_modes.canvas_sessions["-42:d2"]["last_preview_bytes"] == b"duel-image"
        assert crocodile_modes.canvas_sessions["-43:t1"]["drawer_id"] == 4
    finally:
        crocodile_modes.duel_games.clear()
        crocodile_modes.telephone_games.clear()
        crocodile_modes.canvas_sessions.clear()


def test_duel_accepts_only_one_concurrent_correct_answer(monkeypatch):
    from games import crocodile, crocodile_modes
    from games import crocodile_party_controls as controls
    import features.crocodile_scoring as scoring

    chat_id = "-42"
    duel = {
        "phase": "drawing",
        "host_id": 1,
        "artists": [(1, "Первый"), (2, "Второй")],
        "votes": {},
        "word": "кот",
        "started_at": 1.0,
        "finished": set(),
    }
    crocodile_modes.duel_games[chat_id] = duel
    add_point = MagicMock()

    async def slow_artist_record(*args, **kwargs):
        await asyncio.sleep(0)

    start_vote = AsyncMock()
    monkeypatch.setattr(crocodile, "add_point", add_point)
    monkeypatch.setattr(scoring, "record_artist_success", slow_artist_record)
    monkeypatch.setattr(controls.party_state, "persist_party_modes", MagicMock())
    monkeypatch.setattr(controls, "bot", SimpleNamespace(send_message=AsyncMock()))
    monkeypatch.setattr(crocodile_modes, "_start_duel_vote", start_vote)
    first = _message(-42, 10, "Угадавший 1")
    second = _message(-42, 20, "Угадавший 2")

    async def run_both():
        return await asyncio.gather(
            controls.check_duel_answer_locked(first),
            controls.check_duel_answer_locked(second),
        )

    try:
        results = asyncio.run(run_both())
        assert sorted(results) == [False, True]
        assert add_point.call_count == 1
        assert start_vote.await_count == 1
        assert duel["phase"] == "answer_resolved"
    finally:
        crocodile_modes.duel_games.clear()


def test_active_telephone_can_be_cancelled_by_host(monkeypatch):
    from games import crocodile_modes
    from games import crocodile_party_controls as controls

    chat_id = "-42"
    game = {
        "phase": "playing",
        "host_id": 1,
        "players": [(1, "А"), (2, "Б"), (3, "В")],
        "chain": [],
        "step": 0,
    }
    crocodile_modes.telephone_games[chat_id] = game
    crocodile_modes.canvas_sessions[f"{chat_id}:t0"] = {
        "mode": "telephone", "telephone_chat_id": chat_id
    }
    callback = SimpleNamespace(
        data=f"ctel_cancel_{chat_id}",
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
    )
    monkeypatch.setattr(controls.party_state, "persist_party_modes", MagicMock())
    monkeypatch.setattr(controls, "_close_synthetic_room", AsyncMock())
    monkeypatch.setattr(controls, "bot", SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(controls.handle_telephone_callback_resilient(callback))

    assert chat_id not in crocodile_modes.telephone_games
    assert f"{chat_id}:t0" not in crocodile_modes.canvas_sessions
    callback.answer.assert_awaited_once_with("Отменено")


def test_telephone_skip_removes_absent_player_without_flipping_step_parity(monkeypatch):
    from games import crocodile_modes
    from games import crocodile_party_controls as controls

    chat_id = "-42"
    game = {
        "phase": "playing",
        "host_id": 1,
        "players": [(1, "А"), (2, "Пропал"), (3, "В")],
        "chain": [{"kind": "text", "value": "кот", "user_id": 1, "user_name": "А"}],
        "step": 1,
    }
    crocodile_modes.canvas_sessions[f"{chat_id}:t1"] = {
        "mode": "telephone", "telephone_chat_id": chat_id
    }
    resend = AsyncMock()
    monkeypatch.setattr(crocodile_modes, "_send_telephone_step", resend)
    monkeypatch.setattr(controls.party_state, "persist_party_modes", MagicMock())
    monkeypatch.setattr(controls, "_close_synthetic_room", AsyncMock())
    monkeypatch.setattr(controls, "bot", SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(controls._skip_telephone(chat_id, game))

    assert game["step"] == 1
    assert game["players"] == [(1, "А"), (3, "В")]
    assert f"{chat_id}:t1" not in crocodile_modes.canvas_sessions
    resend.assert_awaited_once_with(chat_id, game)


def test_telephone_gallery_is_hidden_until_final_reveal(monkeypatch):
    from games import crocodile_modes
    from games import crocodile_party_controls as controls

    chat_id = "-42"
    game = {
        "phase": "playing",
        "host_id": 1,
        "players": [(1, "А"), (2, "Б"), (3, "В")],
        "chain": [
            {"kind": "text", "value": "кот", "user_id": 1, "user_name": "А"},
            {"kind": "image", "image": b"image", "value": "кот", "user_id": 2, "user_name": "Б"},
        ],
        "step": 2,
    }
    crocodile_modes.telephone_games[chat_id] = game
    archive = AsyncMock()
    monkeypatch.setattr(controls, "_original_record_drawing", archive)
    monkeypatch.setattr(controls.party_state, "persist_party_modes", MagicMock())

    try:
        asyncio.run(
            controls._record_drawing_without_phone_leak(
                chat_id, b"image", "кот", ["Б"], "telephone"
            )
        )
        archive.assert_not_awaited()

        async def reveal_then_finish(cid, active_game):
            assert cid in crocodile_modes.telephone_games
            crocodile_modes.telephone_games.pop(cid, None)

        monkeypatch.setattr(controls, "_original_finish_telephone", reveal_then_finish)
        asyncio.run(controls._finish_telephone_after_reveal(chat_id, game))
        archive.assert_awaited_once_with(chat_id, b"image", "кот", ["Б"], "telephone")
    finally:
        crocodile_modes.telephone_games.clear()


def test_gallery_paginates_beyond_latest_ten(monkeypatch):
    from games import crocodile_party_controls as controls

    rows = [{"chat_id": "-42", "file": f"{index}.jpg"} for index in range(25)]
    monkeypatch.setattr(controls.crocodile_archive, "_load", lambda: rows)

    first, total = controls._gallery_rows_for_page(-42, 0)
    second, _ = controls._gallery_rows_for_page(-42, 1)
    third, _ = controls._gallery_rows_for_page(-42, 2)

    assert total == 25
    assert [row["file"] for row in first] == [f"{i}.jpg" for i in range(15, 25)]
    assert [row["file"] for row in second] == [f"{i}.jpg" for i in range(5, 15)]
    assert [row["file"] for row in third] == [f"{i}.jpg" for i in range(0, 5)]
    nav = controls._gallery_nav_keyboard(1, total)
    callbacks = [button.callback_data for button in nav.inline_keyboard[0]]
    assert callbacks == ["cgal_page_0", "cgal_page_2"]


def test_menu_shows_active_telephone_status_and_controls():
    from games import crocodile_modes
    from games import crocodile_party_controls as controls

    crocodile_modes.telephone_games["-42"] = {
        "phase": "playing",
        "host_id": 1,
        "players": [(1, "А"), (2, "Б"), (3, "В")],
        "chain": [],
        "step": 1,
    }
    try:
        assert "сейчас — Б" in controls.party_status_text(-42)
        callbacks = [
            button.callback_data
            for row in controls.menu_keyboard(-42).inline_keyboard
            for button in row
        ]
        assert "cmenu_skip" in callbacks
        assert "cmenu_stop" in callbacks
        assert "cmenu_gallery" in callbacks
    finally:
        crocodile_modes.telephone_games.clear()
