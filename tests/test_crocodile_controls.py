import asyncio
from pathlib import Path
from types import SimpleNamespace

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

    async def scenario():
        return await controls.start_new_game_with_controls(
            int(CHAT_ID),
            OTHER_ID,
            "Другой",
        )

    try:
        assert asyncio.run(scenario()) is False
        assert crocodile.game_sessions[CHAT_ID]["drawer_id"] == DRAWER_ID
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
    try:
        asyncio.run(controls.handle_callback_with_controls(callback))
        assert crocodile.game_sessions[CHAT_ID]["drawer_id"] == DRAWER_ID
        assert callback.answers[0][1]["show_alert"] is True
    finally:
        crocodile.game_sessions.clear()


def test_previous_word_button_and_history():
    keyboard = controls.get_game_keyboard_with_previous(int(CHAT_ID))
    callback_data = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert f"cr_p_{CHAT_ID}" in callback_data

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


def test_bootstrap_installs_controls_before_restoring_crocodile_sessions():
    source = (
        Path(__file__).resolve().parents[1] / "app" / "bootstrap.py"
    ).read_text(encoding="utf-8")

    configure_pos = source.index("configure_crocodile_controls()")
    restore_pos = source.index("restore_crocodile_sessions()")
    assert configure_pos < restore_pos
