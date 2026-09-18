"""Tests for the GigaChat text2image image provider."""

import asyncio
import base64
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)
from AI import gigachat_image as gi


def test_decode_image_content_supports_base64_and_raw_bytes():
    raw = b"\xff\xd8\xfffake-jpeg"
    encoded = base64.b64encode(raw).decode("ascii")

    assert gi._decode_image_content(encoded) == raw
    assert gi._decode_image_content(raw) == raw


def test_generate_gigachat_image_extracts_file_and_logs_usage(monkeypatch):
    raw = b"\xff\xd8\xffgenerated-image"

    class FakeClient:
        def chat(self, payload):
            return SimpleNamespace(
                model="GigaChat-2:2.0.test",
                usage=SimpleNamespace(
                    prompt_tokens=624,
                    completion_tokens=47,
                    total_tokens=671,
                ),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='<img src="file-123" fuse="true"/> готово'
                        )
                    )
                ],
            )

        def get_image(self, file_id):
            assert file_id == "file-123"
            return SimpleNamespace(content=base64.b64encode(raw).decode("ascii"))

    monkeypatch.setattr(gi, "gigachat_image_client", FakeClient())

    assert gi._generate_gigachat_image_sync("красный гриб") == raw


def test_generate_gigachat_image_returns_none_without_img(monkeypatch):
    class FakeClient:
        def chat(self, payload):
            return SimpleNamespace(
                model="GigaChat-2",
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content="не получилось"))],
            )

    monkeypatch.setattr(gi, "gigachat_image_client", FakeClient())

    assert gi._generate_gigachat_image_sync("гриб") is None


def test_compat_adapter_keeps_old_pun_contract(monkeypatch):
    raw = b"\x89PNG\r\n\x1a\nimage"
    monkeypatch.setattr(gi, "_generate_gigachat_image_sync", lambda prompt: raw)

    adapter = gi.GigaChatImageCompatAPI()
    assert adapter.get_pipeline() == "GigaChat-2"

    request_id, error = adapter.generate("гибрид кот+арбуз", "GigaChat-2")
    assert error is None
    assert request_id

    image, error = adapter.check(request_id)
    assert error is None
    assert image == raw


def test_install_replaces_kandinsky_provider_for_pun():
    module = SimpleNamespace(PIPELINE_ID="old", kandinsky_api=object())

    gi.install_into_picgeneration(module)

    assert module.PIPELINE_ID is None
    assert isinstance(module.kandinsky_api, gi.GigaChatImageCompatAPI)
    assert callable(module.robust_image_generation)
    assert callable(module.handle_kandinsky_generation_command)


def _processing_message():
    class ProcessingMessage:
        def __init__(self):
            self.edits = []
            self.deleted = False

        async def edit_text(self, text):
            self.edits.append(text)

        async def delete(self):
            self.deleted = True

    return ProcessingMessage()


def test_waterfall_prefers_gigachat_without_translation_or_reserves(monkeypatch):
    sent = []

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fallback provider must not run when GigaChat succeeds")

    async def send_generated_photo(message, data, filename):
        sent.append((data, filename))

    module = SimpleNamespace(
        PIPELINE_ID="old",
        kandinsky_api=object(),
        translate_to_en=fail_if_called,
        pollinations_generate=fail_if_called,
        hf_generate=fail_if_called,
        cf_generate_t2i=fail_if_called,
        send_generated_photo=send_generated_photo,
    )
    gi.install_into_picgeneration(module)

    async def fake_gigachat(prompt):
        assert prompt == "красный гриб"
        return b"generated-by-gigachat"

    monkeypatch.setattr(gi, "generate_gigachat_image", fake_gigachat)

    processing = _processing_message()
    asyncio.run(
        module.robust_image_generation(
            message=object(),
            prompt_ru="красный гриб",
            processing_msg=processing,
        )
    )

    assert processing.edits == ["Использую ебучий GigaChat..."]
    assert processing.deleted is True
    assert sent == [(b"generated-by-gigachat", "gigachat.png")]


def test_direct_waterfall_uses_horde_second(monkeypatch):
    from AI import aihorde_image

    sent = []
    calls = []

    async def translate(prompt):
        calls.append(("translate", prompt))
        return "translated mushroom"

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("provider after AI Horde must not run")

    async def send_generated_photo(message, data, filename):
        sent.append((data, filename))

    module = SimpleNamespace(
        PIPELINE_ID="old",
        kandinsky_api=object(),
        translate_to_en=translate,
        pollinations_generate=fail_if_called,
        hf_generate=fail_if_called,
        cf_generate_t2i=fail_if_called,
        send_generated_photo=send_generated_photo,
    )
    gi.install_into_picgeneration(module)

    async def fake_gigachat(prompt):
        calls.append(("gigachat", prompt))
        return None

    async def fake_horde(prompt):
        calls.append(("aihorde", prompt))
        return b"generated-by-horde"

    monkeypatch.setattr(gi, "generate_gigachat_image", fake_gigachat)
    monkeypatch.setattr(aihorde_image, "generate_aihorde_image", fake_horde)

    processing = _processing_message()
    asyncio.run(
        module.robust_image_generation(
            message=object(),
            prompt_ru="красный гриб",
            processing_msg=processing,
        )
    )

    assert calls == [
        ("gigachat", "красный гриб"),
        ("translate", "красный гриб"),
        ("aihorde", "translated mushroom"),
    ]
    assert processing.edits == [
        "Использую ебучий GigaChat...",
        "Использую ебучий AI Horde...",
    ]
    assert processing.deleted is True
    assert sent == [(b"generated-by-horde", "aihorde.webp")]
