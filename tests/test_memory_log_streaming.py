import asyncio
import builtins
import inspect
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler


FAKE_ENV = {
    "API_TOKEN": "123456789:AAFakeTokenForMemoryTestsOnly_abcdefg",
    **{f"GENERIC_API_KEY{i if i else ''}": "fake" for i in ["", 2, 3, 4, 5, 6, 8, 9, 10]},
    "GOOGLE_API_KEY": "fake",
    "GOOGLE_API_KEY2": "fake",
    "GROQ_API_KEY": "fake",
    "OPENROUTER_API_KEY": "fake",
    "SILICONFLOW_API_KEY": "fake",
    "POLLINATIONS_API_KEY": "fake",
}
os.environ.update(FAKE_ENV)


def _log_line(
    timestamp: str,
    chat_id: int,
    text: str,
    *,
    chat_name: str = "Тестовый чат",
    user_id: int = 42,
    username: str = "tester",
    display_name: str = "Тестер",
) -> str:
    return (
        f"{timestamp} - Chat {chat_id} ({chat_name}) - User {user_id} "
        f"({username}) [{display_name}]: {text}\n"
    )


def test_get_chat_messages_streams_without_read_or_readlines(tmp_path, monkeypatch):
    from AI.summarize import _get_chat_messages

    log_path = tmp_path / "user_messages.log"
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write("garbage that must be ignored\n")
        handle.write(
            _log_line(
                "2026-99-99T25:61:61.000000",
                -1001,
                "битая дата",
                chat_name="Имя из битой строки",
            )
        )
        handle.write(_log_line("2026-08-31T23:59:59.000000", -1001, "слишком старое"))
        handle.write(_log_line("2026-09-02T12:00:00.000000", -2002, "чужой чат"))
        handle.write(_log_line("2026-09-02T12:01:00.000000", -1001, "первое", user_id=1, username="alice", display_name="Alice"))
        handle.write(_log_line("2026-09-02T12:02:00.000000", -1001, "второе", user_id=2, username="None", display_name="Bob"))

    real_open = builtins.open

    class IterationOnlyFile:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._wrapped.__exit__(exc_type, exc, tb)

        def __iter__(self):
            return iter(self._wrapped)

        def read(self, *args, **kwargs):
            raise AssertionError("full-file read() is forbidden")

        def readlines(self, *args, **kwargs):
            raise AssertionError("full-file readlines() is forbidden")

    def guarded_open(path, *args, **kwargs):
        wrapped = real_open(path, *args, **kwargs)
        if os.fspath(path) == os.fspath(log_path):
            return IterationOnlyFile(wrapped)
        return wrapped

    monkeypatch.setattr(builtins, "open", guarded_open)

    messages, users, chat_name = _get_chat_messages(
        str(log_path),
        "-1001",
        datetime(2026, 9, 1),
    )

    assert [message["text"] for message in messages] == ["первое", "второе"]
    assert users == {"1": {"username": "alice", "display_name": "Alice"}}
    # Legacy behavior: chat_name comes from the first non-empty target-chat
    # line even if its timestamp is malformed.
    assert chat_name == "Имя из битой строки"


def test_get_chat_messages_large_synthetic_log_keeps_bounded_sample(tmp_path):
    from AI.summarize import _get_chat_messages

    log_path = tmp_path / "large_user_messages.log"
    with log_path.open("w", encoding="utf-8") as handle:
        for index in range(30_000):
            chat_id = -1001 if index % 2 == 0 else -2002
            handle.write(
                _log_line(
                    f"2026-09-02T12:{(index // 60) % 60:02d}:{index % 60:02d}.000000",
                    chat_id,
                    f"message-{index}",
                    user_id=(index % 20) + 1,
                    username=f"user{index % 20}",
                    display_name=f"User {index % 20}",
                )
            )

    messages, users, chat_name = _get_chat_messages(
        str(log_path),
        "-1001",
        datetime(2026, 9, 1),
        200,
        50,
    )

    assert len(messages) == 200
    assert len(users) == 10
    assert chat_name == "Тестовый чат"
    expected_recent = [f"message-{index}" for index in range(29_900, 30_000, 2)]
    assert [message["text"] for message in messages[-50:]] == expected_recent


def test_direct_user_message_log_readers_do_not_use_readlines():
    import AI.birthday_calendar as birthday_calendar
    import AI.quiz as quiz
    import AI.summarize as summarize
    import services.memegenerator as memegenerator

    modules = (summarize, quiz, birthday_calendar, memegenerator)
    for module in modules:
        source = inspect.getsource(module)
        assert ".readlines(" not in source, module.__name__


def test_lexicon_chat_stats_stream_and_match_join_semantics(tmp_path, monkeypatch):
    import features.lexicon_settings as lexicon

    log_path = tmp_path / "lexicon.log"
    log_path.write_text(
        "".join(
            [
                _log_line("2026-09-02T12:00:00.000000", -1001, "крокодилище бобрище"),
                _log_line("2026-09-02T12:01:00.000000", -1001, "бобрище упупище"),
                _log_line("2026-09-02T12:02:00.000000", -2002, "крокодилище чужое"),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(lexicon, "LOG_FILE", log_path)

    words = asyncio.run(lexicon.get_chat_frequent_words(-1001, top_n=10))
    phrases = asyncio.run(lexicon.get_chat_frequent_phrases(-1001, n=2, top_n=10))

    assert dict(words)["бобрище"] == 2
    phrase_counts = dict(phrases)
    assert phrase_counts["крокодилище бобрище"] == 1
    assert phrase_counts["бобрище бобрище"] == 1
    assert phrase_counts["бобрище упупище"] == 1


def test_profile_extractor_is_bounded_but_keeps_recent_tail(tmp_path, monkeypatch):
    import features.lexicon_settings as lexicon

    log_path = tmp_path / "profile.log"
    with log_path.open("w", encoding="utf-8") as handle:
        for index in range(1000):
            handle.write(
                _log_line(
                    "2026-09-02T12:00:00.000000",
                    -1001,
                    f"profile-{index}",
                )
            )
    monkeypatch.setattr(lexicon, "LOG_FILE", log_path)

    sample = asyncio.run(
        lexicon.extract_chat_messages(
            -1001,
            sample_size=50,
            recent_size=10,
        )
    )

    assert len(sample) == 50
    assert sample[-10:] == [f"profile-{index}" for index in range(990, 1000)]


def test_application_log_uses_bounded_rotation():
    import core.logging_setup as logging_setup

    handler = logging_setup._file_handler
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 25 * 1024 * 1024
    assert handler.backupCount == 4
