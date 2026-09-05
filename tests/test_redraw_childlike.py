"""Regression tests for the strict childlike `перерисуй` mode."""

from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from tests import test_smoke_imports  # noqa: F401  (env + mocks)

from AI import picgeneration as pg
from AI import redraw_childlike as redraw


def test_childlike_prompt_puts_bad_drawing_style_first():
    description = (
        "A man in a blue jacket stands beside a red bicycle in a city park, "
        "with trees, a bench and a dog in the background."
    )

    prompt = redraw.build_childlike_redraw_prompt(description)

    assert prompt.startswith("BAD CHILD DRAWING.")
    assert "unskilled 4-6 year old child" in prompt[:250]
    assert "NO professional children's-book illustration" in prompt
    assert "NO polished cartoon" in prompt
    assert prompt.index("SCENE TO COPY:") > prompt.index("NO polished cartoon")
    assert description in prompt


def test_gigachat_redraw_prompt_keeps_style_without_trigger_wording():
    description = "A smiling person holds a yellow umbrella beside a small dog on a street."

    prompt = redraw.build_gigachat_redraw_prompt(description)

    assert prompt.startswith("ROUGH CRAYON DOODLE.")
    assert "kindergarten-style" in prompt
    assert "wobbly thick outlines" in prompt
    assert description in prompt
    assert "4-6 year old child" not in prompt
    assert "wrong anatomy" not in prompt


def test_pollinations_prompt_budget_is_not_legacy_200_chars():
    prompt = "BAD CHILD DRAWING. " + ("crooked crayons " * 35) + "SCENE_END_MARKER"
    prepared = redraw._prepare_pollinations_prompt(prompt)

    assert len(prepared) > 200
    assert "SCENE_END_MARKER" in prepared
    assert len(prepared) <= redraw.POLLINATIONS_PROMPT_MAX_CHARS


@pytest.mark.anyio
async def test_pollinations_url_contains_text_beyond_200_chars(monkeypatch):
    seen_urls = []

    async def fake_queue():
        return ["flux"]

    class Response:
        status_code = 402
        content = b'{"error":"payment required"}'

    def fake_get(url, **kwargs):
        seen_urls.append(url)
        return Response()

    monkeypatch.setattr(pg, "get_free_image_model_queue", fake_queue)
    monkeypatch.setattr(redraw.requests, "get", fake_get)

    prompt = "BAD CHILD DRAWING. " + ("wobbly line " * 30) + "SCENE_END_MARKER"
    assert await redraw.pollinations_generate(prompt) is None

    assert len(seen_urls) == 1
    decoded_url = unquote(seen_urls[0])
    assert "SCENE_END_MARKER" in decoded_url


@pytest.mark.anyio
async def test_redraw_handler_sends_provider_specific_prompts_without_enhancer(monkeypatch):
    captured = {}
    photo = object()

    class ProcessingMessage:
        async def edit_text(self, text):
            captured["processing_text"] = text

    class Message:
        chat = SimpleNamespace(id=-100123)

        async def reply(self, text):
            captured.setdefault("replies", []).append(text)
            return ProcessingMessage()

    async def fake_extract(message, command):
        assert command == "перерисуй"
        return photo, ""

    async def fake_download(bot, attachment):
        assert attachment is photo
        return b"image"

    async def fake_analyze(image_bytes, analysis_prompt, active_model, chat_id):
        assert image_bytes == b"image"
        assert "20-40 words" in analysis_prompt
        return "A smiling person holds a yellow umbrella beside a small dog on a street."

    async def fake_generate(
        message,
        prompt,
        processing_msg,
        skip_translate=False,
        gigachat_prompt=None,
    ):
        captured["prompt"] = prompt
        captured["gigachat_prompt"] = gigachat_prompt
        captured["skip_translate"] = skip_translate
        return "ok"

    monkeypatch.setattr(pg, "extract_image_and_prompt", fake_extract)
    monkeypatch.setattr(pg, "download_telegram_image", fake_download)
    monkeypatch.setattr(pg, "analyze_image_for_redraw", fake_analyze)
    monkeypatch.setattr(pg, "robust_image_generation", fake_generate)
    monkeypatch.setattr(pg, "get_active_model", lambda chat_id: "gemini")

    result = await redraw.handle_redraw_command(Message())

    assert result == "ok"
    assert captured["skip_translate"] is True
    assert captured["prompt"].startswith("BAD CHILD DRAWING.")
    assert "SCENE TO COPY: A smiling person" in captured["prompt"]
    assert "professional children's-book illustration" in captured["prompt"]
    assert captured["gigachat_prompt"].startswith("ROUGH CRAYON DOODLE.")
    assert "SCENE TO COPY: A smiling person" in captured["gigachat_prompt"]
    assert "4-6 year old child" not in captured["gigachat_prompt"]
    assert "wrong anatomy" not in captured["gigachat_prompt"]


def test_install_replaces_redraw_and_pollinations_handlers():
    fake_module = SimpleNamespace()
    redraw.install_into_picgeneration(fake_module)

    assert fake_module.handle_redraw_command is redraw.handle_redraw_command
    assert fake_module.pollinations_generate is redraw.pollinations_generate
