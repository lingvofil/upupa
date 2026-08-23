import ast
import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
from aiogram.types import BufferedInputFile
from PIL import Image

# Настраивает fake env и тяжёлые моки до импорта services.search.
from tests import test_smoke_imports  # noqa: F401

import services.search as search


class RecordingMessage:
    def __init__(self):
        self.photo = None
        self.document = None

    async def reply_photo(self, photo=None, *args, **kwargs):
        self.photo = photo if photo is not None else args[0]

    async def reply_document(self, document=None, *args, **kwargs):
        self.document = document if document is not None else args[0]


def test_search_images_offloads_google_sdk(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args):
        calls.append((func, args))
        return ["https://example.com/cat.jpg"]

    monkeypatch.setattr(search.asyncio, "to_thread", fake_to_thread)
    search.recent_images_cache.clear()

    result = asyncio.run(search.search_images("cat", randomize=False))

    assert result == ["https://example.com/cat.jpg"]
    assert calls == [(search._search_images_sync, ("cat", 1))]


def test_model_generation_is_offloaded(monkeypatch):
    calls = []

    async def fake_to_thread(func, *args):
        calls.append((func, args))
        return SimpleNamespace(text="описание")

    monkeypatch.setattr(search.asyncio, "to_thread", fake_to_thread)

    success, text = asyncio.run(search.generate_image_description(b"jpeg"))

    assert success is True
    assert text == "описание"
    assert len(calls) == 1
    assert calls[0][0] == search.model.generate_content


def test_download_image_with_headers_uses_async_http_client():
    async def scenario():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["referer"] == "https://www.google.com/"
            return httpx.Response(
                200,
                headers={"Content-Type": "image/jpeg"},
                content=b"image-bytes",
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await search.download_image_with_headers(
                "https://example.com/cat.jpg",
                client=client,
            )

    assert asyncio.run(scenario()) == b"image-bytes"


def test_media_senders_keep_bytes_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    image_message = RecordingMessage()
    gif_message = RecordingMessage()

    async def scenario():
        await asyncio.gather(
            search.save_and_send_searched_image(image_message, b"image-data"),
            search.save_and_send_gif(gif_message, b"gif-data"),
        )

    asyncio.run(scenario())

    assert isinstance(image_message.photo, BufferedInputFile)
    assert image_message.photo.data == b"image-data"
    assert isinstance(gif_message.document, BufferedInputFile)
    assert gif_message.document.data == b"gif-data"
    assert list(tmp_path.iterdir()) == []


def test_overlay_text_returns_jpeg_bytes_without_temp_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = BytesIO()
    Image.new("RGB", (500, 300), "white").save(source, format="JPEG")

    result = search.overlay_text_on_image(source.getvalue(), "тест")

    assert result.startswith(b"\xff\xd8")
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


def test_search_async_functions_have_no_known_blocking_calls():
    path = Path(search.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    assert "import requests" not in source
    violations = []
    for function in (
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    ):
        for call in (
            node for node in ast.walk(function) if isinstance(node, ast.Call)
        ):
            name = _call_name(call)
            if (
                name == "open"
                or name == "model.generate_content"
                or name.endswith(".execute")
            ):
                violations.append(f"{function.name}: {name}")

    assert not violations, "Блокирующие вызовы внутри async-функций:\n" + "\n".join(violations)
