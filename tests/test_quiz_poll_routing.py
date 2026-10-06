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



def test_stop_quiz_closes_poll_and_clears_state():
    from AI import quiz

    chat_id = -100123
    chat_id_str = str(chat_id)
    quiz.quiz_questions[chat_id_str] = [
        {"text": "Q", "options": ["A", "B"], "correct_answer": "A"}
    ]
    quiz.quiz_states[chat_id_str] = {
        "current_question": 0,
        "poll_id": "poll-1",
        "message_id": 77,
        "generation": 1,
    }
    quiz._quiz_generations[chat_id_str] = 1
    stop_calls = []

    class FakeBot:
        async def stop_poll(self, *, chat_id, message_id):
            stop_calls.append((chat_id, message_id))

    stopped = asyncio.run(quiz.stop_quiz(FakeBot(), chat_id))

    assert stopped is True
    assert stop_calls == [(chat_id, 77)]
    assert chat_id_str not in quiz.quiz_states
    assert chat_id_str not in quiz.quiz_questions
    assert quiz._quiz_generations[chat_id_str] == 2

    quiz._quiz_generations.pop(chat_id_str, None)


def test_poll_answer_does_not_advance_after_quiz_stop(monkeypatch):
    from AI import quiz

    chat_id = -100124
    chat_id_str = str(chat_id)
    quiz.quiz_questions[chat_id_str] = [
        {"text": "Q1", "options": ["A", "B"], "correct_answer": "A"},
        {"text": "Q2", "options": ["A", "B"], "correct_answer": "B"},
    ]
    quiz.quiz_states[chat_id_str] = {
        "current_question": 0,
        "poll_id": "poll-1",
        "message_id": 78,
        "generation": 1,
    }
    quiz._quiz_generations[chat_id_str] = 1
    sent_polls = []

    class FakeBot:
        async def stop_poll(self, *, chat_id, message_id):
            return None

        async def send_poll(self, **kwargs):
            sent_polls.append(kwargs)
            raise AssertionError("next quiz poll must not be sent after stop")

    bot = FakeBot()

    async def fake_sleep(_seconds):
        await quiz.stop_quiz(bot, chat_id)

    monkeypatch.setattr(quiz.asyncio, "sleep", fake_sleep)

    asyncio.run(
        quiz.process_poll_answer(
            SimpleNamespace(poll_id="poll-1"),
            bot,
        )
    )

    assert sent_polls == []
    assert chat_id_str not in quiz.quiz_states
    assert chat_id_str not in quiz.quiz_questions

    quiz._quiz_generations.pop(chat_id_str, None)


def test_send_question_closes_poll_if_stop_wins_race():
    from AI import quiz

    chat_id = -100125
    chat_id_str = str(chat_id)
    quiz.quiz_questions[chat_id_str] = [
        {"text": "Q1", "options": ["A", "B"], "correct_answer": "A"}
    ]
    quiz._quiz_generations[chat_id_str] = 1
    stop_calls = []

    class FakeBot:
        async def send_poll(self, **kwargs):
            await quiz.stop_quiz(self, chat_id)
            return SimpleNamespace(
                poll=SimpleNamespace(id="late-poll"),
                message_id=79,
            )

        async def stop_poll(self, *, chat_id, message_id):
            stop_calls.append((chat_id, message_id))

        async def send_message(self, *args, **kwargs):
            raise AssertionError("send_question should not fail")

    asyncio.run(quiz.send_question(FakeBot(), chat_id, 0, generation=1))

    assert stop_calls == [(chat_id, 79)]
    assert chat_id_str not in quiz.quiz_states
    assert chat_id_str not in quiz.quiz_questions

    quiz._quiz_generations.pop(chat_id_str, None)


def test_quiz_stop_handler_replies_with_result(monkeypatch):
    from handlers import games

    answers = []

    async def fake_stop_quiz(bot, chat_id):
        assert chat_id == -100126
        return True

    async def answer(text):
        answers.append(text)

    monkeypatch.setattr(games, "stop_quiz", fake_stop_quiz)
    message = SimpleNamespace(
        bot=object(),
        chat=SimpleNamespace(id=-100126),
        answer=answer,
    )

    asyncio.run(games.stop_quiz_text(message))

    assert answers == ["🛑 Викторина остановлена."]
