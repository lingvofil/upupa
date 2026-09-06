"""Coverage for provider calls that historically bypassed LazyResource."""

import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + provider mocks)

from AI import gigachat_image
from AI import media_execution
from infrastructure.ai import clients
from infrastructure.ai import gemini


def test_gigachat_image_routes_through_shared_governor(monkeypatch):
    calls = []

    def fake_governor(operation, func, *args, **kwargs):
        calls.append((operation, kwargs.get("timeout_seconds")))
        return func(*args)

    monkeypatch.setattr(gigachat_image, "run_ai_provider_call", fake_governor)
    monkeypatch.setattr(
        gigachat_image,
        "_generate_gigachat_image_sync",
        lambda prompt: f"image:{prompt}".encode(),
    )

    result = asyncio.run(gigachat_image.generate_gigachat_image("bird"))

    assert result == b"image:bird"
    assert calls == [
        ("gigachat-image.generate", gigachat_image.GIGACHAT_IMAGE_REQUEST_TIMEOUT_SECONDS)
    ]


def test_gigachat_compat_adapter_uses_same_governor(monkeypatch):
    calls = []

    def fake_governor(operation, func, *args, **kwargs):
        calls.append(operation)
        return func(*args)

    monkeypatch.setattr(gigachat_image, "run_ai_provider_call", fake_governor)
    monkeypatch.setattr(
        gigachat_image,
        "_generate_gigachat_image_sync",
        lambda prompt: b"image",
    )

    adapter = gigachat_image.GigaChatImageCompatAPI()
    request_id, error = adapter.generate("pun", "GigaChat-2")

    assert error is None
    assert request_id
    assert calls == ["gigachat-image.generate"]


def test_picgeneration_media_wrappers_use_shared_governor(monkeypatch):
    calls = []

    def fake_governor(operation, func, *args, **kwargs):
        calls.append((operation, kwargs.get("timeout_seconds")))
        return func(*args)

    monkeypatch.setattr(media_execution, "run_ai_provider_call", fake_governor)

    async def pollinations(prompt):
        return b"pollinations"

    async def huggingface(prompt, model_id):
        return b"hf"

    async def cloudflare(prompt):
        return b"cf"

    module = SimpleNamespace(
        pollinations_generate=pollinations,
        hf_generate=huggingface,
        cf_generate_t2i=cloudflare,
        generate_gradio_img2img=lambda *_: None,
        handle_edit_command=lambda *_: None,
    )
    media_execution.install_into_picgeneration(module)

    assert asyncio.run(module.pollinations_generate("x")) == b"pollinations"
    assert asyncio.run(module.hf_generate("x", "model")) == b"hf"
    assert asyncio.run(module.cf_generate_t2i("x")) == b"cf"

    assert [item[0] for item in calls] == [
        "pollinations.image.generate",
        "huggingface.image.generate",
        "cloudflare.image.generate",
    ]
    assert module._media_governor_installed is True


def test_videogeneration_media_wrappers_bound_entire_waterfall(monkeypatch):
    calls = []

    def fake_governor(operation, func, *args, **kwargs):
        calls.append((operation, kwargs.get("timeout_seconds")))
        return func(*args)

    monkeypatch.setattr(media_execution, "run_ai_provider_call", fake_governor)

    async def hf_video(image, prompt):
        return b"hf-video", "ok"

    async def pollinations_video(prompt, start_frame_url=None, duration=5):
        return b"pollinations-video", "wan"

    module = SimpleNamespace(
        generate_hf_zerogpu_video=hf_video,
        generate_video=pollinations_video,
    )
    media_execution.install_into_videogeneration(module)

    assert asyncio.run(module.generate_hf_zerogpu_video(b"img", "move")) == (
        b"hf-video",
        "ok",
    )
    assert asyncio.run(module.generate_video("scene")) == (
        b"pollinations-video",
        "wan",
    )
    assert calls == [
        ("huggingface.zerogpu.video", media_execution.HF_VIDEO_REQUEST_TIMEOUT_SECONDS),
        ("pollinations.video.generate", media_execution.POLLINATIONS_VIDEO_REQUEST_TIMEOUT_SECONDS),
    ]


def test_gemini_client_has_explicit_transport_timeout(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(gemini.genai, "Client", FakeClient)

    client = gemini.create_gemini_client("secret")

    assert isinstance(client, FakeClient)
    assert captured["api_key"] == "secret"
    assert captured["http_options"].timeout == gemini.GEMINI_HTTP_TIMEOUT_MS


def test_direct_gemini_lazy_factory_uses_bounded_factory(monkeypatch):
    captured = []
    marker = object()
    monkeypatch.setattr(clients, "PRIMARY_GEMINI_KEY", "key")
    monkeypatch.setattr(
        clients,
        "create_gemini_client",
        lambda api_key: captured.append(api_key) or marker,
    )

    assert clients._build_gemini_client() is marker
    assert captured == ["key"]
