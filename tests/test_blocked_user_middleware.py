import asyncio
from types import SimpleNamespace

from core.settings import BLOCKED_USERS
import core.middlewares as middlewares
from core.middlewares import BlockedUserMiddleware


def _run(coro):
    return asyncio.run(coro)


async def _dispatch(event):
    middleware = BlockedUserMiddleware()
    calls = []

    async def handler(received_event, data):
        calls.append((received_event, data))
        return "handled"

    result = await middleware(handler, event, {"marker": True})
    return result, calls


def test_blocks_mev515_username_case_insensitively():
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=999001, username="MeV515")
    )

    result, calls = _run(_dispatch(event))

    assert result is None
    assert calls == []


def test_blocks_existing_numeric_user_id():
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=BLOCKED_USERS[0], username="someone_else")
    )

    result, calls = _run(_dispatch(event))

    assert result is None
    assert calls == []


def test_blocks_username_on_nested_callback_update():
    event = SimpleNamespace(
        from_user=None,
        callback_query=SimpleNamespace(
            from_user=SimpleNamespace(id=999002, username="MEV515")
        ),
    )

    result, calls = _run(_dispatch(event))

    assert result is None
    assert calls == []


def test_allows_unblocked_user():
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=999003, username="ordinary_user")
    )

    result, calls = _run(_dispatch(event))

    assert result == "handled"
    assert len(calls) == 1


def test_username_block_persists_numeric_id(tmp_path, monkeypatch):
    blocked_path = tmp_path / "blocked_users.json"
    monkeypatch.setattr(middlewares, "_blocked_users_path", blocked_path)
    monkeypatch.setattr(middlewares, "_persisted_blocked_user_ids", set())
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=515515, username="MEV515")
    )

    result, calls = _run(_dispatch(event))

    assert result is None
    assert calls == []
    assert blocked_path.read_text(encoding="utf-8") == "[515515]"

    # A username change must not bypass the block after the ID has been pinned.
    renamed_event = SimpleNamespace(
        from_user=SimpleNamespace(id=515515, username="renamed_user")
    )
    renamed_result, renamed_calls = _run(_dispatch(renamed_event))
    assert renamed_result is None
    assert renamed_calls == []
