import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401


def _audio_response(data: bytes = b"pcm"):
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[SimpleNamespace(inline_data=SimpleNamespace(data=data))]
                )
            )
        ]
    )


def test_radio_upupa_alias_is_accepted_without_duration_suffix():
    import handlers.radio as radio_handler

    assert radio_handler.is_radio_command("радио упупа")
    assert radio_handler.is_radio_command("Радио Упупа")
    assert not radio_handler.is_radio_command("Радио Упупа 3")


def test_dual_voice_radio_uses_one_pooled_gemini_request_for_short_script(monkeypatch):
    import features.radio.voices as voices
    import services.speech as speech

    calls = []

    class FakeGeminiModel:
        def generate_content(self, text, **kwargs):
            calls.append((text, kwargs))
            return _audio_response()

    monkeypatch.setattr(voices, "radio_gemini_model", FakeGeminiModel())
    monkeypatch.setattr(voices.random, "sample", lambda _items, _count: ["Leda", "Puck"])
    monkeypatch.setattr(speech, "_pcm_to_wav_bytes", lambda pcm: b"wav-" + pcm)
    monkeypatch.setattr(speech, "_merge_wav_chunks_to_mp3", lambda chunks: b"mp3-" + b"".join(chunks))

    result = asyncio.run(
        voices.synthesize_two_voice_radio(
            "ВЕДУЩИЙ: Первый заход. ЭКСПЕРТ: Ответ эксперта. ВЕДУЩИЙ: Финал."
        )
    )

    assert result is not None
    assert result.provider == "gemini-dual"
    assert result.chunks == 1
    assert result.data == b"mp3-wav-pcm"
    assert len(calls) == 1
    transcript, kwargs = calls[0]
    assert "HOST: Первый заход." in transcript
    assert "EXPERT: Ответ эксперта." in transcript
    config = kwargs["generation_config"]["speech_config"]
    assert config["language_code"] == "ru-RU"
    speakers = config["multi_speaker_voice_config"]["speaker_voice_configs"]
    assert [speaker["speaker"] for speaker in speakers] == ["HOST", "EXPERT"]
    assert kwargs["require_text"] is False


def test_long_dual_voice_radio_is_split_into_bounded_requests(monkeypatch):
    import features.radio.voices as voices
    import services.speech as speech

    calls = []

    class FakeGeminiModel:
        def generate_content(self, text, **kwargs):
            calls.append((text, kwargs))
            return _audio_response(str(len(calls)).encode())

    monkeypatch.setattr(voices, "radio_gemini_model", FakeGeminiModel())
    monkeypatch.setattr(voices.random, "sample", lambda _items, _count: ["Leda", "Puck"])
    monkeypatch.setattr(speech, "_pcm_to_wav_bytes", lambda pcm: b"wav-" + pcm)
    monkeypatch.setattr(speech, "_merge_wav_chunks_to_mp3", lambda chunks: b"mp3-" + b"".join(chunks))

    host_text = " ".join(["ведущий рассказывает новость."] * 55)
    expert_text = " ".join(["эксперт комментирует событие."] * 55)
    result = asyncio.run(
        voices.synthesize_two_voice_radio(
            f"ВЕДУЩИЙ: {host_text} ЭКСПЕРТ: {expert_text}"
        )
    )

    assert result is not None
    assert result.chunks == len(calls)
    assert len(calls) > 1
    assert all(len(text) <= voices.RADIO_TTS_CHUNK_CHARS for text, _kwargs in calls)
    assert all(text.startswith(("HOST: ", "EXPERT: ")) for text, _kwargs in calls)
    assert any("HOST:" in text for text, _kwargs in calls)
    assert any("EXPERT:" in text for text, _kwargs in calls)


def test_radio_quota_error_does_not_repeat_same_episode_as_single_voice(monkeypatch):
    import features.radio.service as radio_service
    import features.radio.voices as voices

    dual = AsyncMock(side_effect=voices.RadioTTSQuotaError("quota exhausted"))
    single = AsyncMock()
    monkeypatch.setattr(radio_service, "synthesize_two_voice_radio", dual)
    monkeypatch.setattr(radio_service, "synthesize_single_voice_radio", single)

    with pytest.raises(voices.RadioTTSQuotaError):
        asyncio.run(
            radio_service._synthesize_radio_script(
                "ВЕДУЩИЙ: Текст. ЭКСПЕРТ: Ответ."
            )
        )

    dual.assert_awaited_once()
    single.assert_not_awaited()


def test_radio_single_voice_fallback_uses_persistent_pool(monkeypatch):
    import features.radio.voices as voices
    import services.speech as speech

    calls = []

    class FakeGeminiModel:
        def generate_content(self, text, **kwargs):
            calls.append((text, kwargs))
            return _audio_response(b"single")

    monkeypatch.setattr(voices, "radio_gemini_model", FakeGeminiModel())
    monkeypatch.setattr(voices.random, "choice", lambda _items: "Erinome")
    monkeypatch.setattr(speech, "_pcm_to_wav_bytes", lambda pcm: b"wav-" + pcm)
    monkeypatch.setattr(speech, "_merge_wav_chunks_to_mp3", lambda chunks: b"mp3-" + b"".join(chunks))

    result = asyncio.run(
        voices.synthesize_single_voice_radio("ВЕДУЩИЙ: Привет. ЭКСПЕРТ: Пока.")
    )

    assert result.provider == "gemini"
    assert result.chunks == 1
    assert len(calls) == 1
    assert calls[0][0] == "Привет. Пока."
    config = calls[0][1]["generation_config"]["speech_config"]
    assert config["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Erinome"


def test_long_single_voice_fallback_is_split_into_bounded_requests(monkeypatch):
    import features.radio.voices as voices
    import services.speech as speech

    calls = []

    class FakeGeminiModel:
        def generate_content(self, text, **kwargs):
            calls.append((text, kwargs))
            return _audio_response(b"single")

    monkeypatch.setattr(voices, "radio_gemini_model", FakeGeminiModel())
    monkeypatch.setattr(voices.random, "choice", lambda _items: "Erinome")
    monkeypatch.setattr(speech, "_pcm_to_wav_bytes", lambda pcm: b"wav-" + pcm)
    monkeypatch.setattr(speech, "_merge_wav_chunks_to_mp3", lambda chunks: b"mp3-" + b"".join(chunks))

    text = " ".join(["длинная радионовость с подробностями."] * 80)
    result = asyncio.run(voices.synthesize_single_voice_radio(f"ВЕДУЩИЙ: {text}"))

    assert result.chunks == len(calls)
    assert len(calls) > 1
    assert all(len(chunk) <= voices.RADIO_TTS_CHUNK_CHARS for chunk, _kwargs in calls)
