import ast
import asyncio
import inspect
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Configure fake env and mocks for heavy optional dependencies before imports.
from tests import test_smoke_imports  # noqa: F401

import AI.voice as voice
import features.interactive_settings as interactive_settings
import features.radio.script as radio_script
import features.radio.service as radio_service
import handlers
import handlers.radio as radio_handler
import services.speech as speech
from core.state import chat_settings


@pytest.fixture(autouse=True)
def restore_chat_settings():
    saved = deepcopy(chat_settings)
    yield
    chat_settings.clear()
    chat_settings.update(saved)


def _message(text: str, *, name: str = "Вася") -> dict:
    return {
        "date": "25.08",
        "username": name.lower(),
        "display_name": name,
        "text": text,
    }


def test_radio_command_variants_and_router_order():
    assert radio_handler.is_radio_command("радио упупы")
    assert radio_handler.is_radio_command("Упупа радио")
    assert radio_handler.is_radio_command("Упупа, радио")
    assert not radio_handler.is_radio_command("упупа радиостанция")

    names = [router.name for router in handlers.ROUTERS]
    assert names.index("radio") < names.index("dialog")
    assert names[-1] == "dialog"


def test_disabled_radio_stops_before_episode_build(monkeypatch):
    chat_settings["-1001"] = {"radio_enabled": False}
    reply = AsyncMock()
    message = SimpleNamespace(
        text="радио упупы",
        chat=SimpleNamespace(id=-1001),
        reply=reply,
    )
    build = AsyncMock()
    monkeypatch.setattr(radio_handler, "build_radio_episode", build)

    asyncio.run(radio_handler.handle_radio_command(message))

    build.assert_not_awaited()
    reply.assert_awaited_once()
    assert "отключено администраторами" in reply.await_args.args[0]


def test_collect_radio_history_expands_window_then_rejects_sparse_history(monkeypatch):
    calls = []

    def fake_get_chat_messages(_path, _chat_id, threshold):
        calls.append(threshold)
        return [_message("ок") for _ in range(2)], {}, "Тестовый чат"

    monkeypatch.setattr(radio_service, "_get_chat_messages", fake_get_chat_messages)

    with pytest.raises(radio_service.RadioHistoryError):
        asyncio.run(
            radio_service.collect_radio_history(
                "-1001",
                log_file_path="unused.log",
                now=datetime(2026, 8, 25, 0, 0, 0),
            )
        )

    assert len(calls) == 3
    spans = [(datetime(2026, 8, 25, 0, 0, 0) - threshold).total_seconds() / 3600 for threshold in calls]
    assert spans == [24, 72, 168]


def test_collect_radio_history_uses_24_hours_when_enough(monkeypatch):
    calls = []
    messages = [_message("Это достаточно содержательное сообщение про жизнь чата номер один.") for _ in range(8)]

    def fake_get_chat_messages(_path, _chat_id, threshold):
        calls.append(threshold)
        return messages, {}, "Тестовый чат"

    monkeypatch.setattr(radio_service, "_get_chat_messages", fake_get_chat_messages)
    result, chat_name, hours = asyncio.run(
        radio_service.collect_radio_history(
            "-1001",
            log_file_path="unused.log",
            now=datetime(2026, 8, 25, 0, 0, 0),
        )
    )

    assert result == messages
    assert chat_name == "Тестовый чат"
    assert hours == 24
    assert len(calls) == 1


def test_normal_radio_script_uses_dedicated_spoken_prompt(monkeypatch):
    prompts = []

    async def fake_generate(prompt, chat_id, **kwargs):
        prompts.append((prompt, chat_id, kwargs))
        return "В эфире Упупа. Вася обсуждал арбуз, Петя спорил про лёд. На этом всё."

    monkeypatch.setattr(radio_script, "_generate_with_active_model", fake_generate)
    messages = [
        _message("Арбуз надо заморозить", name="Вася"),
        _message("Нет, сначала нужен лёд", name="Петя"),
    ]

    result = asyncio.run(radio_script.generate_radio_script("-1001", "Чятище", messages, 24))

    assert result.text.startswith("В эфире Упупа")
    assert result.word_count == len(result.text.split())
    assert not result.used_structured_summary
    assert len(prompts) == 1
    prompt = prompts[0][0]
    assert "Ты — ведущий «Радио Упупы»" in prompt
    assert "Не используй активную пользовательскую персону" in prompt
    assert "Вася: Арбуз надо заморозить" in prompt
    assert "Петя: Нет, сначала нужен лёд" in prompt


def test_large_history_is_summarized_before_final_script(monkeypatch):
    prompts = []

    async def fake_generate(prompt, chat_id, **kwargs):
        prompts.append(prompt)
        if "редакторскую выжимку" in prompt:
            return "В чате долго обсуждали арбузы и лёд. Вася был активнее всех."
        return "В эфире Упупа. Сегодня обсуждали арбузы и лёд. Вася был активнее всех. Конец выпуска."

    monkeypatch.setattr(radio_script, "_generate_with_active_model", fake_generate)
    messages = [
        _message(("длинное сообщение про арбуз и лёд " * 35) + str(index), name="Вася")
        for index in range(30)
    ]

    result = asyncio.run(radio_script.generate_radio_script("-1001", "Чятище", messages, 24))

    assert result.used_structured_summary
    assert len(prompts) == 2
    assert "редакторскую выжимку" in prompts[0]
    assert "Редакторская выжимка:" in prompts[1]


def test_script_sanitization_has_hard_word_limit_and_removes_markup_urls():
    raw = "**Эфир** https://example.com\n- " + "слово " * 700
    cleaned = radio_script.sanitize_radio_script(raw)

    assert len(cleaned.split()) <= radio_script.RADIO_MAX_WORDS
    assert "https://" not in cleaned
    assert "**" not in cleaned


def test_speech_tts_success(monkeypatch):
    async def fake_gemini(_text, _voice):
        return b"wav"

    monkeypatch.setattr(speech, "_synthesize_gemini_chunk", fake_gemini)
    monkeypatch.setattr(speech, "_merge_wav_chunks_to_mp3", lambda chunks: b"merged-" + b"".join(chunks))

    result = asyncio.run(speech.synthesize_speech("Hello from Upupa.", provider_order=("gemini",)))

    assert result.data == b"merged-wav"
    assert result.provider == "gemini"
    assert result.chunks == 1


def test_speech_tts_falls_back_to_groq(monkeypatch):
    async def broken_gemini(_text, _voice):
        raise speech.SpeechSynthesisError("gemini down")

    async def working_groq(_text, _voice):
        return b"groq-wav"

    monkeypatch.setattr(speech, "_synthesize_gemini_chunk", broken_gemini)
    monkeypatch.setattr(speech, "_synthesize_groq_chunk", working_groq)
    monkeypatch.setattr(speech, "_merge_wav_chunks_to_mp3", lambda chunks: b"mp3-" + b"".join(chunks))

    result = asyncio.run(
        speech.synthesize_speech(
            "English fallback text.",
            provider_order=("gemini", "groq"),
        )
    )

    assert result.provider == "groq"
    assert result.data == b"mp3-groq-wav"


def test_radio_pipeline_has_no_distortion_dependency():
    modules = (radio_handler, radio_service, radio_script, speech)
    combined = "\n".join(inspect.getsource(module) for module in modules)

    assert "apply_ffmpeg_audio_distortion" not in combined
    assert "services.distortion" not in combined
    assert "apply_ffmpeg_audio_distortion" in inspect.getsource(voice)


def test_legacy_voice_temp_directory_is_cleaned(monkeypatch, tmp_path):
    chat_settings["-1001"] = {"active_model": "gemini"}
    monkeypatch.setattr(voice, "update_chat_settings", lambda _chat_id: None)
    monkeypatch.setattr(
        voice,
        "generate_text_response_for_voice",
        AsyncMock(return_value="Привет, это обычное голосовое."),
    )
    monkeypatch.setattr(
        voice,
        "synthesize_speech",
        AsyncMock(return_value=speech.SpeechAudio(b"clean", "mp3", "gemini", 1)),
    )

    temp_parents = []

    async def fake_distortion(input_path, output_path, _intensity):
        input_file = Path(input_path)
        output_file = Path(output_path)
        assert input_file.exists()
        temp_parents.append(input_file.parent)
        output_file.write_bytes(b"distorted")
        return True

    monkeypatch.setattr(voice, "apply_ffmpeg_audio_distortion", fake_distortion)

    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    bot = SimpleNamespace(send_chat_action=AsyncMock(), send_voice=AsyncMock())
    message = SimpleNamespace(
        text="упупа скажи привет",
        chat=SimpleNamespace(id=-1001),
        message_id=123,
        reply=AsyncMock(return_value=status),
    )

    asyncio.run(voice.handle_voice_command(message, bot))

    bot.send_voice.assert_awaited_once()
    assert temp_parents
    assert all(not path.exists() for path in temp_parents)


def test_radio_telegram_send_error_is_user_facing(monkeypatch):
    chat_settings["-1001"] = {"radio_enabled": True}
    episode = radio_service.RadioEpisode(
        audio=b"mp3",
        script="В эфире Упупа.",
        word_count=3,
        estimated_seconds=2,
        period_hours=24,
        message_count=10,
        tts_provider="gemini",
        tts_chunks=1,
    )
    monkeypatch.setattr(radio_handler, "build_radio_episode", AsyncMock(return_value=episode))

    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    bot = SimpleNamespace(
        send_chat_action=AsyncMock(),
        send_voice=AsyncMock(side_effect=RuntimeError("telegram rejected voice")),
    )
    message = SimpleNamespace(
        text="радио упупы",
        chat=SimpleNamespace(id=-1001),
        message_id=555,
        bot=bot,
        reply=AsyncMock(return_value=status),
    )

    asyncio.run(radio_handler.handle_radio_command(message))

    status.edit_text.assert_awaited_once()
    assert "Telegram отказался" in status.edit_text.await_args.args[0]


def test_radio_setting_toggle_is_persisted(monkeypatch):
    chat_settings["-1001"] = {}
    saved = []
    monkeypatch.setattr(interactive_settings, "has_settings_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(interactive_settings, "save_chat_settings", lambda: saved.append(deepcopy(chat_settings)))
    monkeypatch.setattr(
        interactive_settings,
        "get_main_settings_markup",
        AsyncMock(return_value=("settings", "markup")),
    )

    query = SimpleNamespace(
        data="settings:toggle:radio",
        from_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    asyncio.run(interactive_settings.handle_settings_callback(query))

    assert chat_settings["-1001"]["radio_enabled"] is False
    assert saved[-1]["-1001"]["radio_enabled"] is False


def test_radio_async_pipeline_has_no_direct_blocking_io_calls():
    forbidden_names = {"open", "sleep", "run", "Popen", "read_bytes", "write_bytes"}
    modules = (radio_service, radio_script, speech)

    violations = []
    for module in modules:
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith)):
                continue
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in forbidden_names:
                    # Calls deliberately passed as callables to asyncio.to_thread
                    # are Attribute/Name nodes, not Call nodes, so a Call here is direct.
                    violations.append((module.__name__, node.name, name))

    assert violations == []
