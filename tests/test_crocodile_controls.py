import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401
from games import crocodile
from games import crocodile_controls as controls


CHAT_ID = "-1001707530786"
DRAWER_ID = 101
OTHER_ID = 202


def _session(*, started_at=1_000.0):
    return {
        "word": "капибара",
        "drawer_id": DRAWER_ID,
        "drawer_name": "Художник",
        "preview_message_id": 777,
        "last_preview_time": 0,
        "last_preview_bytes": b"jpeg",
        "bump_task": None,
        "started_at": started_at,
        "previous_words": [],
    }


def test_stop_lock_handler_configurator_drives_stable_entrypoint():
    original = controls.get_stop_lock_remaining_seconds_handler()

    def handler(session, user_id, *, now=None):
        return 17.5

    try:
        controls.configure_stop_lock_remaining_seconds_handler(handler)
        assert controls.stop_lock_remaining_seconds({}, OTHER_ID, now=123.0) == 17.5
    finally:
        controls.configure_stop_lock_remaining_seconds_handler(original)


def test_crocodile_stop_is_immediate_for_drawer_and_delayed_for_others():
    session = _session(started_at=1_000.0)

    assert controls.STOP_UNLOCK_SECONDS == 5 * 60
    assert controls.can_stop_round(session, DRAWER_ID, now=1_001.0) is True
    assert controls.can_stop_round(session, OTHER_ID, now=1_299.9) is False
    assert controls.can_stop_round(session, OTHER_ID, now=1_300.0) is True


def test_legacy_round_without_started_at_is_not_relocked_after_restart():
    session = _session()
    session.pop("started_at")

    assert controls.can_stop_round(session, OTHER_ID, now=1_001.0) is True


def test_starting_another_crocodile_cannot_bypass_stop_lock(monkeypatch):
    crocodile.game_sessions[CHAT_ID] = _session(started_at=1_000.0)
    monkeypatch.setattr(controls.time, "time", lambda: 1_100.0)
    downstream = AsyncMock()

    async def scenario():
        return await controls.start_new_game_with_controls(
            int(CHAT_ID),
            OTHER_ID,
            "Другой",
            downstream,
        )

    try:
        assert asyncio.run(scenario()) is False
        downstream.assert_not_awaited()
        assert crocodile.game_sessions[CHAT_ID]["drawer_id"] == DRAWER_ID
    finally:
        crocodile.game_sessions.clear()


def test_controls_start_initializes_round_state_after_base_start(monkeypatch):
    monkeypatch.setattr(controls.time, "time", lambda: 1_234.5)

    async def base_start(chat_id, user_id, user_name):
        crocodile.game_sessions[str(chat_id)] = {
            "word": "барсук",
            "drawer_id": user_id,
            "drawer_name": user_name,
        }

    try:
        result = asyncio.run(
            controls.start_new_game_with_controls(
                int(CHAT_ID),
                DRAWER_ID,
                "Художник",
                base_start,
            )
        )
        session = crocodile.game_sessions[CHAT_ID]
        assert result is True
        assert session["started_at"] == 1_234.5
        assert session["previous_words"] == []
    finally:
        crocodile.game_sessions.clear()


def test_command_start_keeps_internal_base_controls_path(monkeypatch):
    async def base_start(chat_id, user_id, user_name):
        crocodile.game_sessions[str(chat_id)] = {
            "word": "барсук",
            "drawer_id": user_id,
            "drawer_name": user_name,
        }

    base = AsyncMock(side_effect=base_start)
    public_entrypoint = AsyncMock()
    monkeypatch.setattr(controls, "_base_start_new_game", base)
    monkeypatch.setattr(crocodile, "start_new_game", public_entrypoint)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=int(CHAT_ID)),
        from_user=SimpleNamespace(id=DRAWER_ID, full_name="Художник"),
        reply=AsyncMock(),
    )

    try:
        asyncio.run(controls.handle_start_game_with_controls(message))
        base.assert_awaited_once_with(int(CHAT_ID), DRAWER_ID, "Художник")
        public_entrypoint.assert_not_awaited()
    finally:
        crocodile.game_sessions.clear()


def test_text_stop_is_rejected_for_other_user_before_five_minutes(monkeypatch):
    crocodile.game_sessions[CHAT_ID] = _session(started_at=1_000.0)
    monkeypatch.setattr(controls.time, "time", lambda: 1_100.0)

    class FakeMessage:
        chat = SimpleNamespace(id=int(CHAT_ID))
        from_user = SimpleNamespace(id=OTHER_ID)
        replies = []

        async def reply(self, text):
            self.replies.append(text)

    message = FakeMessage()
    try:
        asyncio.run(controls.handle_text_stop_with_controls(message))
        assert crocodile.game_sessions[CHAT_ID]["drawer_id"] == DRAWER_ID
        assert message.replies
        assert "только Художник" in message.replies[0]
        assert "через 4 мин." in message.replies[0]
    finally:
        crocodile.game_sessions.clear()


def test_stop_button_is_rejected_for_other_user_before_five_minutes(monkeypatch):
    crocodile.game_sessions[CHAT_ID] = _session(started_at=1_000.0)
    monkeypatch.setattr(controls.time, "time", lambda: 1_100.0)

    class FakeCallback:
        data = f"cr_stop_{CHAT_ID}"
        from_user = SimpleNamespace(id=OTHER_ID)
        answers = []

        async def answer(self, text, **kwargs):
            self.answers.append((text, kwargs))

    callback = FakeCallback()
    downstream = AsyncMock()
    try:
        asyncio.run(controls.handle_callback_with_controls(callback, downstream))
        assert crocodile.game_sessions[CHAT_ID]["drawer_id"] == DRAWER_ID
        assert callback.answers[0][1]["show_alert"] is True
        downstream.assert_not_awaited()
    finally:
        crocodile.game_sessions.clear()


def test_controls_callback_delegates_unknown_action_once():
    callback = SimpleNamespace(data="unrelated")
    downstream = AsyncMock(return_value="handled-downstream")

    result = asyncio.run(controls.handle_callback_with_controls(callback, downstream))

    assert result == "handled-downstream"
    downstream.assert_awaited_once_with(callback)


def test_previous_word_button_and_history():
    base_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Холст", url="https://example.com")],
            [
                InlineKeyboardButton(text="👁 Слово", callback_data=f"cr_w_{CHAT_ID}"),
                InlineKeyboardButton(text="⏭ Другое", callback_data=f"cr_n_{CHAT_ID}"),
                InlineKeyboardButton(text="🛑 Стоп", callback_data=f"cr_stop_{CHAT_ID}"),
            ],
        ]
    )
    keyboard = controls.decorate_game_keyboard_with_previous(
        int(CHAT_ID),
        base_keyboard,
    )
    callback_data = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert f"cr_p_{CHAT_ID}" in callback_data
    assert f"cr_stop_{CHAT_ID}" in callback_data
    assert keyboard.inline_keyboard[1][1].callback_data == f"cr_n_{CHAT_ID}"

    session = _session()
    controls.remember_current_word(session)
    session["word"] = "самолёт"
    controls.remember_current_word(session)
    session["word"] = "крокодил"

    assert controls.take_previous_word(session) == "самолёт"
    assert controls.take_previous_word(session) == "капибара"
    assert controls.take_previous_word(session) is None


def test_control_fields_are_persisted_with_active_session():
    session = _session(started_at=1234.5)
    session["previous_words"] = ["кот", "дом"]

    record = controls.session_to_record_with_controls(CHAT_ID, session)
    assert record["started_at"] == 1234.5
    assert record["previous_words"] == ["кот", "дом"]

    restored_chat_id, restored = controls.session_from_record_with_controls(record)
    assert restored_chat_id == CHAT_ID
    assert restored["started_at"] == 1234.5
    assert restored["previous_words"] == ["кот", "дом"]


def test_runtime_installs_controls_before_bootstrap_restores_crocodile_sessions():
    root = Path(__file__).resolve().parents[1]
    runtime_source = (root / "games" / "crocodile_runtime.py").read_text(encoding="utf-8")
    bootstrap_source = (root / "app" / "bootstrap.py").read_text(encoding="utf-8")

    assert (
        "configure_crocodile_controls(base_start_new_game=base_start_new_game)"
        in runtime_source
    )
    configure_pos = bootstrap_source.index("configure_crocodile_runtime()")
    restore_pos = bootstrap_source.index("restore_crocodile_sessions()")
    assert configure_pos < restore_pos
