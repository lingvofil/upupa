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


def test_duo_persistence_roundtrip_keeps_both_drawers(monkeypatch):
    from games import crocodile_party_state as state

    monkeypatch.setattr(
        state,
        "_original_session_to_record",
        lambda chat_id, session: {
            "chat_id": chat_id,
            "word": session["word"],
            "drawer_id": session["drawer_id"],
        },
    )
    monkeypatch.setattr(
        state,
        "_original_session_from_record",
        lambda record: (
            str(record["chat_id"]),
            {"word": record["word"], "drawer_id": record["drawer_id"]},
        ),
    )
    session = {
        "word": "кот",
        "drawer_id": 11,
        "drawer_name": "Первый + Второй",
        "drawer_ids": [11, 22],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
    }

    record = state._session_to_record_with_duo("-42", session)
    chat_id, restored = state._session_from_record_with_duo(record)

    assert chat_id == "-42"
    assert restored["drawer_ids"] == [11, 22]
    assert restored["drawer_names"] == ["Первый", "Второй"]
    assert restored["mode"] == "duo"


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
    monkeypatch.setattr(controls.bot, "send_message", AsyncMock())
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
    monkeypatch.setattr(controls.bot, "send_message", AsyncMock())

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
    monkeypatch.setattr(controls.bot, "send_message", AsyncMock())

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
