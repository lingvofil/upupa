import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx

# Настраивает fake env и тяжёлые моки до импорта AI.whatisthere.
from tests import test_smoke_imports  # noqa: F401

import AI.whatisthere as whatisthere


def test_download_url_bytes_uses_async_http_and_enforces_limit():
    async def scenario():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["user-agent"] == "test-agent"
            return httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg"},
                content=b"12345",
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            data, content_type = await whatisthere._download_url_bytes(
                "https://example.com/image.jpg",
                headers={"User-Agent": "test-agent"},
                max_bytes=10,
                client=client,
            )
            assert data == b"12345"
            assert content_type == "image/jpeg"

            try:
                await whatisthere._download_url_bytes(
                    "https://example.com/image.jpg",
                    headers={"User-Agent": "test-agent"},
                    max_bytes=4,
                    client=client,
                )
            except ValueError as exc:
                assert "лимит" in str(exc)
            else:
                raise AssertionError("oversized response must fail")

    asyncio.run(scenario())


def test_download_file_keeps_compatibility_and_writes_async(tmp_path, monkeypatch):
    async def fake_download(file_id):
        assert file_id == "file-1"
        return b"telegram-bytes"

    monkeypatch.setattr(whatisthere, "_download_telegram_bytes", fake_download)
    target = tmp_path / "media.bin"

    assert asyncio.run(whatisthere.download_file("file-1", str(target))) is True
    assert target.read_bytes() == b"telegram-bytes"


def test_generate_text_offloads_sync_provider(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return "groq-result"

    monkeypatch.setattr(whatisthere.asyncio, "to_thread", fake_to_thread)

    result = asyncio.run(
        whatisthere._generate_text_with_active_model("prompt", "groq", 123)
    )

    assert result == "groq-result"
    assert calls == [(whatisthere.groq_ai.generate_text, ("prompt",), {})]


def test_analyze_media_offloads_gemini(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return SimpleNamespace(text="gemini-result")

    monkeypatch.setattr(whatisthere.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(whatisthere, "get_active_model", lambda chat_id: "gemini")

    result = asyncio.run(
        whatisthere.analyze_media_bytes(
            b"image-data",
            "image/jpeg",
            "что здесь",
            chat_id=123,
        )
    )

    assert result == "gemini-result"
    assert len(calls) == 1
    assert calls[0][0] == whatisthere.model.generate_content


def test_media_pipeline_uses_bytes_without_temp_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = {}

    async def fake_download(file_id):
        assert file_id == "photo-id"
        return b"photo-bytes"

    async def fake_analyze(
        media_source,
        mime_type,
        custom_prompt=None,
        chat_id=None,
        *,
        source_name=None,
    ):
        seen.update(
            media_source=media_source,
            mime_type=mime_type,
            chat_id=chat_id,
            source_name=source_name,
        )
        return "описание"

    monkeypatch.setattr(whatisthere, "_download_telegram_bytes", fake_download)
    monkeypatch.setattr(whatisthere, "analyze_media_bytes", fake_analyze)

    photo = SimpleNamespace(file_id="photo-id")
    message = SimpleNamespace(
        reply_to_message=None,
        photo=[photo],
        text="чотам",
        caption=None,
        chat=SimpleNamespace(id=777),
    )

    success, description = asyncio.run(
        whatisthere.process_image_whatisthere(message)
    )

    assert success is True
    assert description == "описание"
    assert seen == {
        "media_source": b"photo-bytes",
        "mime_type": "image/jpeg",
        "chat_id": 777,
        "source_name": "photo_photo-id.jpg",
    }
    assert list(tmp_path.iterdir()) == []


def _call_name(call: ast.Call) -> str:
    parts = []
    node = call.func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_whatisthere_async_functions_have_no_known_blocking_calls():
    path = Path(whatisthere.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    assert "import requests" not in source

    forbidden = {
        "open",
        "groq_ai.analyze_image",
        "groq_ai.transcribe_audio",
        "groq_ai.generate_text",
        "gigachat_model.generate_content",
        "model.generate_content",
        "robotics_model.generate_content",
    }
    violations = []

    for function in (
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    ):
        for call in (
            node for node in ast.walk(function) if isinstance(node, ast.Call)
        ):
            name = _call_name(call)
            if name in forbidden:
                violations.append(f"{function.name}: {name}")

    assert not violations, (
        "Блокирующие вызовы внутри async-функций:\n" + "\n".join(violations)
    )
