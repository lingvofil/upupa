import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_reverse_crocodile_start_claim_blocks_parallel_and_active_rounds():
    from handlers import games
    from games import reverse_crocodile

    chat_id = -100
    games._reverse_croc_starts_in_progress.discard(chat_id)
    reverse_crocodile.games.pop(str(chat_id), None)

    try:
        assert games._claim_reverse_croc_start(chat_id) is True
        assert games._claim_reverse_croc_start(chat_id) is False

        games._release_reverse_croc_start(chat_id)
        reverse_crocodile.games[str(chat_id)] = {"word": "кот"}
        assert games._claim_reverse_croc_start(chat_id) is False
    finally:
        games._release_reverse_croc_start(chat_id)
        reverse_crocodile.games.pop(str(chat_id), None)


def test_reverse_crocodile_multiple_again_taps_start_only_one_round(monkeypatch):
    from handlers import games
    from games import reverse_crocodile

    chat_id = -100
    games._reverse_croc_starts_in_progress.discard(chat_id)
    reverse_crocodile.games.pop(str(chat_id), None)

    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_start_game(_message):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        reverse_crocodile.games[str(chat_id)] = {"word": "кот"}

    monkeypatch.setattr(reverse_crocodile, "start_game", fake_start_game)

    cb1 = SimpleNamespace(
        data="rcroc_again_0",
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
        answer=AsyncMock(),
    )
    cb2 = SimpleNamespace(
        data="rcroc_again_0",
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
        answer=AsyncMock(),
    )

    async def scenario():
        first = asyncio.create_task(games.reverse_croc_callback(cb1))
        await started.wait()
        second = asyncio.create_task(games.reverse_croc_callback(cb2))
        await asyncio.sleep(0)

        assert calls == 1
        cb2.answer.assert_awaited_once_with(
            "Новый раунд уже запускается или идёт.",
            show_alert=True,
        )

        release.set()
        await asyncio.gather(first, second)

    try:
        asyncio.run(scenario())
        assert calls == 1
        cb1.answer.assert_awaited_once_with("Рисую новое...")
    finally:
        games._release_reverse_croc_start(chat_id)
        reverse_crocodile.games.pop(str(chat_id), None)


def test_old_again_button_cannot_replace_active_round(monkeypatch):
    from handlers import games
    from games import reverse_crocodile

    chat_id = -100
    games._reverse_croc_starts_in_progress.discard(chat_id)
    reverse_crocodile.games[str(chat_id)] = {"word": "кот"}
    start = AsyncMock()
    monkeypatch.setattr(reverse_crocodile, "start_game", start)

    callback = SimpleNamespace(
        data="rcroc_again_0",
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
        answer=AsyncMock(),
    )

    try:
        asyncio.run(games.reverse_croc_callback(callback))
        start.assert_not_awaited()
        callback.answer.assert_awaited_once_with(
            "Новый раунд уже запускается или идёт.",
            show_alert=True,
        )
    finally:
        games._release_reverse_croc_start(chat_id)
        reverse_crocodile.games.pop(str(chat_id), None)
