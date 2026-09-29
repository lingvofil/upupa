import asyncio
from types import SimpleNamespace

import httpx
import pytest

from services.elevenlabs import (
    ElevenLabsClient,
    ElevenLabsNoVoicesError,
    ElevenLabsQuotaError,
    ElevenLabsTemporaryError,
    ElevenLabsTimeoutError,
    ElevenLabsVoice,
)
import services.revoice as revoice


class FakeMessage:
    def __init__(self, text="переозвучь", *, reply_to_message=None, message_id=500):
        self.text = text
        self.reply_to_message = reply_to_message
        self.message_id = message_id
        self.chat = SimpleNamespace(id=-100123)
        self.from_user = SimpleNamespace(id=42)
        self.replies = []

    async def reply(self, text):
        self.replies.append(text)


class FakeBot:
    def __init__(self):
        self.sent_voices = []

    async def send_voice(self, **kwargs):
        self.sent_voices.append(kwargs)


class FakeElevenLabsClient:
    def __init__(self):
        self.cleanup_calls = 0
        self.random_calls = 0
        self.design_calls = []
        self.create_calls = []
        self.change_calls = []
        self.delete_calls = []
        self.raise_on_change = None

    async def ensure_stale_temporary_voice_cleanup(self):
        self.cleanup_calls += 1

    async def choose_random_voice(self):
        self.random_calls += 1
        return ElevenLabsVoice("random-voice", "Random")

    async def design_voice(self, description):
        self.design_calls.append(description)
        return ["generated-a", "generated-b"]

    async def create_temporary_voice(self, **kwargs):
        self.create_calls.append(kwargs)
        return "tmp-voice"

    async def voice_change(self, voice_id, audio_bytes):
        self.change_calls.append((voice_id, audio_bytes))
        if self.raise_on_change is not None:
            raise self.raise_on_change
        return b"changed-audio"

    async def delete_voice(self, voice_id):
        self.delete_calls.append(voice_id)


def voice_reply(*, duration=10, message_id=400):
    return SimpleNamespace(
        voice=SimpleNamespace(file_id="voice-file", duration=duration),
        message_id=message_id,
    )


def non_voice_reply():
    return SimpleNamespace(
        voice=None,
        audio=SimpleNamespace(file_id="audio-file"),
        message_id=401,
    )


def test_recognizes_revoice_command():
    assert revoice.parse_revoice_command("переозвучь") == ""
    assert revoice.parse_revoice_command(" ПЕРЕОЗВУЧЬ ") == ""
    assert revoice.parse_revoice_command("переозвучься") is None
    assert revoice.parse_revoice_command("тут переозвучь") is None


def test_revoice_without_parameters_is_random_mode():
    assert revoice.parse_revoice_command("переозвучь") == ""


def test_extracts_custom_prompt():
    assert (
        revoice.parse_revoice_command("переозвучь голосом старой ведьмы")
        == "голосом старой ведьмы"
    )
    description = revoice.prepare_voice_description("ведьма")
    assert description.startswith("ведьма")
    assert len(description) >= 20


def test_command_without_reply_gets_short_hint():
    message = FakeMessage(reply_to_message=None)
    asyncio.run(revoice.handle_revoice_command(message, FakeBot(), client=FakeElevenLabsClient()))
    assert message.replies == [
        "Ответь командой «переозвучь» на голосовое сообщение."
    ]


def test_reply_to_non_voice_is_rejected():
    message = FakeMessage(reply_to_message=non_voice_reply())
    asyncio.run(revoice.handle_revoice_command(message, FakeBot(), client=FakeElevenLabsClient()))
    assert message.replies == [
        "Переозвучивать пока умею только голосовые сообщения."
    ]


def test_reply_to_voice_returns_voice_reply_to_original(monkeypatch):
    async def fake_download(_bot, _file_id):
        return b"source-audio"

    monkeypatch.setattr(revoice, "download_telegram_bytes", fake_download)
    client = FakeElevenLabsClient()
    bot = FakeBot()
    message = FakeMessage(reply_to_message=voice_reply(duration=10, message_id=321))

    asyncio.run(revoice.handle_revoice_command(message, bot, client=client))

    assert not message.replies
    assert len(bot.sent_voices) == 1
    assert bot.sent_voices[0]["reply_to_message_id"] == 321


def test_voice_at_most_30_seconds_is_not_trimmed(monkeypatch):
    async def fake_download(_bot, _file_id):
        return b"source-audio"

    async def fail_trim(*_args, **_kwargs):
        raise AssertionError("trim should not run")

    monkeypatch.setattr(revoice, "download_telegram_bytes", fake_download)
    monkeypatch.setattr(revoice, "trim_voice_to_seconds", fail_trim)

    client = FakeElevenLabsClient()
    message = FakeMessage(reply_to_message=voice_reply(duration=30))
    asyncio.run(revoice.handle_revoice_command(message, FakeBot(), client=client))

    assert client.change_calls == [("random-voice", b"source-audio")]


def test_voice_over_30_seconds_is_trimmed_before_api(monkeypatch):
    events = []

    async def fake_download(_bot, _file_id):
        events.append("download")
        return b"source-audio"

    async def fake_trim(audio_bytes, *, max_seconds):
        events.append(("trim", audio_bytes, max_seconds))
        return b"trimmed-audio"

    monkeypatch.setattr(revoice, "download_telegram_bytes", fake_download)
    monkeypatch.setattr(revoice, "trim_voice_to_seconds", fake_trim)

    client = FakeElevenLabsClient()
    message = FakeMessage(reply_to_message=voice_reply(duration=75))
    asyncio.run(revoice.handle_revoice_command(message, FakeBot(), client=client))

    assert events == [
        "download",
        ("trim", b"source-audio", 30),
    ]
    assert client.change_calls == [("random-voice", b"trimmed-audio")]


def test_random_voice_flow_never_designs_voice(monkeypatch):
    async def fake_download(_bot, _file_id):
        return b"source-audio"

    monkeypatch.setattr(revoice, "download_telegram_bytes", fake_download)
    client = FakeElevenLabsClient()

    asyncio.run(
        revoice.handle_revoice_command(
            FakeMessage(reply_to_message=voice_reply()),
            FakeBot(),
            client=client,
        )
    )

    assert client.cleanup_calls == 1
    assert client.random_calls == 1
    assert client.design_calls == []
    assert client.create_calls == []
    assert client.change_calls == [("random-voice", b"source-audio")]
    assert client.delete_calls == []


def test_custom_voice_design_flow(monkeypatch):
    async def fake_download(_bot, _file_id):
        return b"source-audio"

    monkeypatch.setattr(revoice, "download_telegram_bytes", fake_download)
    monkeypatch.setattr(revoice.random, "choice", lambda values: values[-1])
    client = FakeElevenLabsClient()

    asyncio.run(
        revoice.handle_revoice_command(
            FakeMessage(
                text="переозвучь голосом пьяного гоблина",
                reply_to_message=voice_reply(),
            ),
            FakeBot(),
            client=client,
        )
    )

    assert len(client.design_calls) == 1
    assert "пьяного гоблина" in client.design_calls[0]
    assert client.create_calls[0]["generated_voice_id"] == "generated-b"
    assert client.change_calls == [("tmp-voice", b"source-audio")]


def test_temporary_voice_deleted_after_success(monkeypatch):
    async def fake_download(_bot, _file_id):
        return b"source-audio"

    monkeypatch.setattr(revoice, "download_telegram_bytes", fake_download)
    client = FakeElevenLabsClient()

    asyncio.run(
        revoice.handle_revoice_command(
            FakeMessage(
                text="переозвучь как уставший диктор",
                reply_to_message=voice_reply(),
            ),
            FakeBot(),
            client=client,
        )
    )

    assert client.delete_calls == ["tmp-voice"]


def test_temporary_voice_deleted_after_later_error(monkeypatch):
    async def fake_download(_bot, _file_id):
        return b"source-audio"

    monkeypatch.setattr(revoice, "download_telegram_bytes", fake_download)
    client = FakeElevenLabsClient()
    client.raise_on_change = ElevenLabsTemporaryError("boom")
    message = FakeMessage(
        text="переозвучь очень высоким мультяшным голосом",
        reply_to_message=voice_reply(),
    )

    asyncio.run(revoice.handle_revoice_command(message, FakeBot(), client=client))

    assert client.delete_calls == ["tmp-voice"]
    assert message.replies == ["ElevenLabs сейчас не отвечает. Попробуй позже."]


def test_missing_api_key_does_not_crash_bot(monkeypatch):
    monkeypatch.setattr(revoice, "ELEVENLABS_API_KEY", None)
    monkeypatch.setattr(revoice, "_default_client", None)
    monkeypatch.setattr(revoice, "_default_client_key", None)
    message = FakeMessage(reply_to_message=voice_reply())

    asyncio.run(revoice.handle_revoice_command(message, FakeBot()))

    assert message.replies == ["Переозвучка сейчас недоступна."]


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (429, ElevenLabsQuotaError),
        (503, ElevenLabsTemporaryError),
    ],
)
def test_http_status_errors_are_mapped(status_code, expected_error):
    def handler(request):
        return httpx.Response(status_code, request=request, json={"detail": "error"})

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ElevenLabsClient("fake-key", http_client=http_client)
            with pytest.raises(expected_error):
                await client.get_available_voices(force_refresh=True)

    asyncio.run(scenario())


def test_http_timeout_is_mapped():
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ElevenLabsClient("fake-key", http_client=http_client)
            with pytest.raises(ElevenLabsTimeoutError):
                await client.get_available_voices(force_refresh=True)

    asyncio.run(scenario())


def test_no_available_random_voices_is_reported():
    def handler(request):
        return httpx.Response(
            200,
            request=request,
            json={"voices": [], "has_more": False, "next_page_token": None},
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ElevenLabsClient("fake-key", http_client=http_client)
            with pytest.raises(ElevenLabsNoVoicesError):
                await client.choose_random_voice()

    asyncio.run(scenario())


def test_random_voice_avoids_immediate_repeat_when_possible():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "voices": [
                    {"voice_id": "a", "name": "A"},
                    {"voice_id": "b", "name": "B"},
                ],
                "has_more": False,
                "next_page_token": None,
            },
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            client = ElevenLabsClient("fake-key", http_client=http_client)
            first = await client.choose_random_voice()
            second = await client.choose_random_voice()
            assert first.voice_id != second.voice_id
            assert len(calls) == 1

    asyncio.run(scenario())
