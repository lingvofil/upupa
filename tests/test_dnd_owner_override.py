import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from app.bootstrap import DndOwnerHostOverrideMiddleware
from core.settings import ADMIN_ID


def test_bot_owner_temporarily_gets_dnd_host_rights(monkeypatch):
    chat_id = -1009001
    session = SimpleNamespace(starter_user_id=None)
    dnd.dnd_sessions[chat_id] = session
    persisted = []
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))
    middleware = DndOwnerHostOverrideMiddleware()
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID),
        chat=SimpleNamespace(id=chat_id),
    )

    async def handler(_event, _data):
        assert session.starter_user_id == ADMIN_ID
        return "ok"

    try:
        result = asyncio.run(middleware(handler, event, {}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert result == "ok"
    assert session.starter_user_id is None
    assert persisted == [True]


def test_bot_owner_override_preserves_real_host(monkeypatch):
    chat_id = -1009002
    real_host_id = 42
    session = SimpleNamespace(starter_user_id=real_host_id)
    dnd.dnd_sessions[chat_id] = session
    persisted = []
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))
    middleware = DndOwnerHostOverrideMiddleware()
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
    )

    async def handler(_event, _data):
        assert session.starter_user_id == ADMIN_ID
        return "ok"

    try:
        result = asyncio.run(middleware(handler, event, {}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert result == "ok"
    assert session.starter_user_id == real_host_id
    assert persisted == [True]


def test_non_owner_does_not_get_dnd_host_override(monkeypatch):
    chat_id = -1009003
    real_host_id = 42
    session = SimpleNamespace(starter_user_id=real_host_id)
    dnd.dnd_sessions[chat_id] = session
    persisted = []
    monkeypatch.setattr(dnd, "persist_dnd_sessions", lambda: persisted.append(True))
    middleware = DndOwnerHostOverrideMiddleware()
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=99),
        chat=SimpleNamespace(id=chat_id),
    )

    async def handler(_event, _data):
        assert session.starter_user_id == real_host_id
        return "ok"

    try:
        result = asyncio.run(middleware(handler, event, {}))
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert result == "ok"
    assert session.starter_user_id == real_host_id
    assert persisted == []
