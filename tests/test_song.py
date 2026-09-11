import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import tempfile

import pytest

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _message(text: str, *, username: str = "vasya", name: str = "Вася") -> dict:
    return {
        "date": "11.09",
        "username": username,
        "display_name": name,
        "text": text,
    }


def test_song_command_parser_and_router_order():
    import handlers
    import handlers.song as song_handler

    chat = SimpleNamespace(entities=[])
    chat.text = "песня чат"
    assert song_handler.parse_song_request(chat) == ("chat", None)

    person = SimpleNamespace(text="песня @VaSya_42", entities=[])
    mode, target = song_handler.parse_song_request(person)
    assert mode == "person"
    assert target.username == "VaSya_42"

    unrelated = SimpleNamespace(text="песня хорошая", entities=[])
    assert song_handler.parse_song_request(unrelated) == (None, None)

    names = [router.name for router in handlers.ROUTERS]
    assert names.index("song") < names.index("dialog")


def test_song_text_mention_uses_telegram_user_identity():
    import handlers.song as song_handler

    user = SimpleNamespace(id=77, username=None, full_name="Вася Без Юзернейма", first_name="Вася", is_bot=False)
    entity = SimpleNamespace(type="text_mention", user=user)
    message = SimpleNamespace(text="песня Вася Без Юзернейма", entities=[entity])

    mode, target = song_handler.parse_song_request(message)

    assert mode == "person"
    assert target.user_id == 77
    assert target.username is None
    assert target.display_name == "Вася Без Юзернейма"


def test_song_draft_is_grounded_short_json(monkeypatch):
    import features.song.lyrics as lyrics

    captured = []

    monkeypatch.setattr(
        lyrics,
        "build_prompt_with_current_chat_prompt",
        lambda _chat_id, task_prompt, **_kwargs: task_prompt,
    )

    async def fake_generate(prompt, _chat_id):
        captured.append(prompt)
        return (
            '{"title":"Арбузный профсоюз","style":"raw punk with cheap synths, fast and sarcastic",'
            '"lyrics":"[verse]\\nВася тащит лёд в арбуз\\nПетя спорит про лаймовый сок\\nЧат опять устроил срач\\nБлендер воет как пророк\\n[chorus]\\nРом налили — спор живёт\\nУпупа всё это поёт"}'
        )

    monkeypatch.setattr(lyrics, "_generate_with_active_model", fake_generate)
    draft = asyncio.run(
        lyrics.generate_song_draft(
            "-1001",
            source_context="Вася: лёд в арбуз\nПетя: нужен лаймовый сок",
            subject="Чятище",
            mode="chat",
        )
    )

    assert draft.title == "Арбузный профсоюз"
    assert draft.style_prompt.startswith("raw punk")
    content = [line for line in draft.lyrics.splitlines() if not line.startswith("[")]
    assert 4 <= len(content) <= 8
    assert "ТОЛЬКО из блока" in captured[0]
    assert "1–3 самых заметных свежих сюжета" in captured[0]
    assert "4–8" in captured[0]


def test_song_draft_truncates_extra_lyric_lines():
    from features.song.lyrics import parse_song_draft

    raw_lines = "\\n".join(f"строка {index}" for index in range(1, 13))
    draft = parse_song_draft(
        '{"title":"Тест","style":"garage punk",'
        f'"lyrics":"[verse]\\n{raw_lines}"}}'
    )
    content = [line for line in draft.lyrics.splitlines() if not line.startswith("[")]
    assert len(content) == 8


def test_collect_song_history_expands_when_chat_is_quiet(monkeypatch):
    import features.song.service as service

    calls = []

    def fake_get(_path, _chat_id, threshold, *_sampling):
        calls.append(threshold)
        if len(calls) < 2:
            return [_message("коротко")], {"1": {"username": "vasya", "display_name": "Вася"}}, "Чат"
        messages = [_message("Достаточно содержательное сообщение про свежий чат и его странности.") for _ in range(6)]
        return messages, {"1": {"username": "vasya", "display_name": "Вася"}}, "Чат"

    monkeypatch.setattr(service, "_get_chat_messages", fake_get)
    messages, _users, _name, hours = asyncio.run(
        service.collect_song_history(
            "-1001",
            log_file_path="unused.log",
            now=datetime(2026, 9, 11, 18, 0, 0),
        )
    )

    assert len(messages) == 6
    assert hours == 72
    assert len(calls) == 2


def test_person_context_uses_real_target_messages_and_neighbors():
    import features.song.service as service

    messages = [
        _message("я опять купил замороженный арбуз", username="vasya", name="Вася"),
        _message("ты его хоть разморозь", username="petya", name="Петя"),
        _message("нет я туда ром ебану", username="vasya", name="Вася"),
        _message("сколько рома", username="petya", name="Петя"),
        _message("175 мл на полкило", username="vasya", name="Вася"),
    ]
    users = {
        "10": {"username": "vasya", "display_name": "Вася"},
        "11": {"username": "petya", "display_name": "Петя"},
    }

    context, resolved, count = service.build_person_context(
        "-1001",
        messages,
        users,
        service.SongTarget(username="VaSyA"),
        period_hours=24,
        log_file_path="unused.log",
        now=datetime(2026, 9, 11, 18, 0, 0),
    )

    assert resolved.user_id == 10
    assert count == 3
    assert "замороженный арбуз" in context
    assert "175 мл" in context
    assert "ты его хоть разморозь" in context


def test_yue2_endpoint_arguments_select_no_score_fast_and_seed():
    import features.song.hf_yue2 as yue2

    endpoint = {
        "parameters": [
            {"label": "Style", "parameter_name": "style"},
            {"label": "Lyrics", "parameter_name": "lyrics"},
            {"label": "Symbolic planning", "parameter_name": "planning", "choices": ["Melody + chords", "Melody only", "No score"]},
            {"label": "Render quality", "parameter_name": "quality", "choices": ["Fast · 16 steps", "Best quality · 32 steps"]},
            {"label": "Seed", "parameter_name": "seed", "parameter_default": 42},
        ]
    }

    args = yue2._build_endpoint_args(
        endpoint,
        style_prompt="raw punk",
        lyrics="[verse]\nраз\ндва\nтри\nчетыре",
        seed=123456,
    )

    assert args == (
        "raw punk",
        "[verse]\nраз\ндва\nтри\nчетыре",
        "No score",
        "Fast · 16 steps",
        123456,
    )


def test_yue2_result_prefers_mp3_and_copies_out_of_download_dir(tmp_path):
    import features.song.hf_yue2 as yue2

    flac = tmp_path / "song.flac"
    mp3 = tmp_path / "song.mp3"
    abc = tmp_path / "song.abc"
    flac.write_bytes(b"flac")
    mp3.write_bytes(b"mp3-data")
    abc.write_text("ABC")

    output = yue2._download_or_copy_mp3((str(flac), str(mp3), str(abc)))
    try:
        assert output.suffix == ".mp3"
        assert output.read_bytes() == b"mp3-data"
        assert output != mp3
    finally:
        output.unlink(missing_ok=True)


def test_yue2_quota_retry_hint_is_extracted():
    import features.song.hf_yue2 as yue2

    classified = yue2._classify_exception(RuntimeError("GPU quota exceeded. Try again in 17 minutes."))
    assert isinstance(classified, yue2.Yue2QuotaError)
    assert classified.retry_hint == "17 minutes"


def test_song_audio_temp_file_is_deleted_after_telegram_send():
    import handlers.song as song_handler
    from features.song.lyrics import SongDraft
    from features.song.service import GeneratedSong

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as file:
        file.write(b"mp3")
        path = Path(file.name)

    song = GeneratedSong(
        mp3_path=path,
        draft=SongDraft("Песня-тест", "raw punk", "[verse]\n1\n2\n3\n4"),
        period_hours=24,
        source_messages=10,
    )
    bot = SimpleNamespace(send_chat_action=AsyncMock(), send_audio=AsyncMock())
    message = SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=55, bot=bot)
    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())

    asyncio.run(song_handler._send_song(message, status, song))

    assert not path.exists()
    bot.send_audio.assert_awaited_once()
    assert "Песня-тест" in bot.send_audio.await_args.kwargs["caption"]
    status.delete.assert_awaited_once()


def test_song_audio_temp_file_is_deleted_when_telegram_rejects():
    import handlers.song as song_handler
    from features.song.lyrics import SongDraft
    from features.song.service import GeneratedSong

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as file:
        file.write(b"mp3")
        path = Path(file.name)

    song = GeneratedSong(
        mp3_path=path,
        draft=SongDraft("Песня-тест", "raw punk", "[verse]\n1\n2\n3\n4"),
        period_hours=24,
        source_messages=10,
    )
    bot = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_audio=AsyncMock(side_effect=RuntimeError("Telegram rejected audio")),
    )
    message = SimpleNamespace(chat=SimpleNamespace(id=-1001), message_id=55, bot=bot)
    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())

    asyncio.run(song_handler._send_song(message, status, song))

    assert not path.exists()
    status.edit_text.assert_awaited_once()
    assert "Telegram" in status.edit_text.await_args.args[0]
