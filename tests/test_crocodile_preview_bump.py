import asyncio
from types import SimpleNamespace

from games import crocodile


CHAT_ID = "-1001707530786"


def _session(message_id: int = 101) -> dict:
    return {
        "word": "капибара",
        "drawer_id": 42,
        "drawer_name": "M&M",
        "preview_message_id": message_id,
        "last_preview_time": 0,
        "last_preview_bytes": b"drawing",
        "bump_task": None,
    }


def test_bump_skips_resend_when_old_preview_cannot_be_deleted(monkeypatch):
    sent = []

    async def fail_delete(chat_id, message_id):
        raise RuntimeError("temporary Telegram delete failure")

    async def send_photo(*args, **kwargs):
        sent.append((args, kwargs))
        return SimpleNamespace(message_id=202)

    monkeypatch.setattr(crocodile.bot, "delete_message", fail_delete)
    monkeypatch.setattr(crocodile.bot, "send_photo", send_photo)

    session = _session()
    crocodile.game_sessions[CHAT_ID] = session
    try:
        changed = asyncio.run(crocodile._bump_preview_once(CHAT_ID, session))
    finally:
        crocodile.game_sessions.pop(CHAT_ID, None)

    assert changed is False
    assert sent == []
    assert session["preview_message_id"] == 101


def test_bump_replaces_preview_after_successful_delete(monkeypatch):
    deleted = []

    async def delete_message(chat_id, message_id):
        deleted.append((chat_id, message_id))

    async def send_photo(*args, **kwargs):
        return SimpleNamespace(message_id=202)

    monkeypatch.setattr(crocodile.bot, "delete_message", delete_message)
    monkeypatch.setattr(crocodile.bot, "send_photo", send_photo)

    session = _session()
    crocodile.game_sessions[CHAT_ID] = session
    try:
        changed = asyncio.run(crocodile._bump_preview_once(CHAT_ID, session))
    finally:
        crocodile.game_sessions.pop(CHAT_ID, None)

    assert changed is True
    assert deleted == [(int(CHAT_ID), 101)]
    assert session["preview_message_id"] == 202


def test_parallel_bumps_leave_only_one_visible_preview(monkeypatch):
    visible = {101}
    next_message_id = 101

    async def delete_message(chat_id, message_id):
        await asyncio.sleep(0)
        if message_id not in visible:
            raise RuntimeError("Bad Request: message to delete not found")
        visible.remove(message_id)

    async def send_photo(*args, **kwargs):
        nonlocal next_message_id
        await asyncio.sleep(0)
        next_message_id += 1
        visible.add(next_message_id)
        return SimpleNamespace(message_id=next_message_id)

    monkeypatch.setattr(crocodile.bot, "delete_message", delete_message)
    monkeypatch.setattr(crocodile.bot, "send_photo", send_photo)

    session = _session()
    crocodile.game_sessions[CHAT_ID] = session

    async def scenario():
        return await asyncio.gather(
            crocodile._bump_preview_once(CHAT_ID, session),
            crocodile._bump_preview_once(CHAT_ID, session),
        )

    try:
        results = asyncio.run(scenario())
    finally:
        crocodile.game_sessions.pop(CHAT_ID, None)

    assert results == [True, True]
    assert visible == {session["preview_message_id"]}
    assert len(visible) == 1
