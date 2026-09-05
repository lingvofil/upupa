import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def test_quiz_poll_answer_is_processed_once_when_answers_arrive_together(monkeypatch):
    from handlers import games

    calls = []

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_process_poll_answer(poll_answer, bot):
            calls.append(poll_answer.poll_id)
            entered.set()
            await release.wait()

        monkeypatch.setattr(games, "process_poll_answer", fake_process_poll_answer)
        games._quiz_poll_answers_in_progress.clear()

        first = asyncio.create_task(
            games._process_quiz_poll_answer_once(SimpleNamespace(poll_id="poll-1"), object())
        )
        await entered.wait()

        second = asyncio.create_task(
            games._process_quiz_poll_answer_once(SimpleNamespace(poll_id="poll-1"), object())
        )
        await asyncio.sleep(0)

        release.set()
        results = await asyncio.gather(first, second)
        return results

    results = asyncio.run(scenario())

    assert calls == ["poll-1"]
    assert sorted(results) == [False, True]
    assert games._quiz_poll_answers_in_progress == set()
