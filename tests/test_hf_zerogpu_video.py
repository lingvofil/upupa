"""Tests for the Hugging Face ZeroGPU image-to-video provider."""

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image

from tests import test_smoke_imports  # noqa: F401  (env + heavy-lib mocks)
from AI import hf_zerogpu_video as hfvideo
from AI import videogeneration as vg


def _jpeg_bytes(width=1200, height=800):
    out = BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="JPEG")
    return out.getvalue()


def test_wan_dimensions_preserve_valid_480p_shape():
    height, width = hfvideo._calculate_wan_dimensions(_jpeg_bytes(1200, 800))

    assert 128 <= height <= 896
    assert 128 <= width <= 896
    assert height % 32 == 0
    assert width % 32 == 0
    assert width > height


def test_generate_hf_video_uses_public_space_and_downloads_result(monkeypatch):
    image_bytes = _jpeg_bytes()
    calls = {}

    class FakeJob:
        def __init__(self, output_path):
            self.output_path = output_path

        def result(self, timeout=None):
            calls["timeout"] = timeout
            return (str(self.output_path), 12345)

    class FakeClient:
        def __init__(self, space_id, hf_token=None, verbose=True, download_files=None):
            calls["space_id"] = space_id
            calls["hf_token"] = hf_token
            calls["download_files"] = download_files
            self.download_files = Path(download_files)

        def view_api(self, print_info=True, return_format=None):
            return {
                "named_endpoints": {
                    "/generate_video": {
                        "parameters": [{"component": "Image"}, {"component": "Textbox"}],
                        "returns": [{"component": "Video"}, {"component": "Slider"}],
                    }
                },
                "unnamed_endpoints": {},
            }

        def submit(self, *args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs
            output_path = self.download_files / "generated.mp4"
            output_path.write_bytes(b"fake-mp4")
            return FakeJob(output_path)

        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(hfvideo, "Client", FakeClient)
    monkeypatch.setattr(hfvideo, "handle_file", lambda path: f"uploaded:{path}")
    monkeypatch.setattr(hfvideo, "HUGGINGFACE_TOKEN", "hf_test")

    video, status = asyncio.run(hfvideo.generate_hf_zerogpu_video(image_bytes, "a subtle smile"))

    assert video == b"fake-mp4"
    assert status == "ok"
    assert calls["space_id"] == "multimodalart/wan2-1-fast"
    assert calls["hf_token"] == "hf_test"
    assert calls["kwargs"] == {"api_name": "/generate_video"}
    assert calls["timeout"] == hfvideo.HF_VIDEO_TIMEOUT_SECONDS
    assert calls["closed"] is True


def test_generate_hf_video_classifies_quota_errors(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def view_api(self, **kwargs):
            return {"named_endpoints": {"/generate_video": {}}, "unnamed_endpoints": {}}

        def submit(self, *args, **kwargs):
            raise RuntimeError("You have exceeded your GPU quota")

        def close(self):
            pass

    monkeypatch.setattr(hfvideo, "Client", FakeClient)
    monkeypatch.setattr(hfvideo, "handle_file", lambda path: path)

    video, status = asyncio.run(hfvideo.generate_hf_zerogpu_video(_jpeg_bytes(), "move"))

    assert video is None
    assert status == "quota"


def test_animate_uses_hf_first_without_touching_pollinations(monkeypatch):
    calls = {"upload": 0, "sent": 0}

    class Status:
        def __init__(self):
            self.edits = []
            self.deleted = False

        async def edit_text(self, text):
            self.edits.append(text)

        async def delete(self):
            self.deleted = True

    class Message:
        photo = [object()]
        reply_to_message = None
        text = "оживи слегка улыбнуться"
        caption = None

        class Chat:
            id = 123

        chat = Chat()

        def __init__(self):
            self.status = Status()

        async def reply(self, text):
            self.initial_text = text
            return self.status

        async def reply_video(self, video):
            calls["sent"] += 1

    async def fake_download(bot, photo):
        return b"telegram-image"

    async def fake_hf(image, prompt):
        assert image == b"telegram-image"
        assert "улыбнуться" in prompt
        return b"hf-video", "ok"

    async def forbidden_upload(*args, **kwargs):
        calls["upload"] += 1
        raise AssertionError("Pollinations must not be touched when ZeroGPU succeeds")

    monkeypatch.setattr(vg, "_check_and_count_limit", lambda chat_id: True)
    monkeypatch.setattr(vg, "download_telegram_image", fake_download)
    monkeypatch.setattr(vg, "generate_hf_zerogpu_video", fake_hf)
    monkeypatch.setattr(vg, "upload_media", forbidden_upload)

    message = Message()
    asyncio.run(vg.process_animate_photo(message, bot=object()))

    assert calls["sent"] == 1
    assert calls["upload"] == 0
    assert message.status.deleted is True


def test_animate_falls_back_to_pollinations_after_hf_failure(monkeypatch):
    calls = {"upload": 0, "pollinations": 0, "sent": 0}

    class Status:
        def __init__(self):
            self.edits = []
            self.deleted = False

        async def edit_text(self, text):
            self.edits.append(text)

        async def delete(self):
            self.deleted = True

    class Message:
        photo = [object()]
        reply_to_message = None
        text = "оживи"
        caption = None

        class Chat:
            id = 456

        chat = Chat()

        def __init__(self):
            self.status = Status()

        async def reply(self, text):
            return self.status

        async def reply_video(self, video):
            calls["sent"] += 1

    async def fake_download(bot, photo):
        return b"telegram-image"

    async def fake_hf(image, prompt):
        return None, "quota"

    async def fake_upload(image):
        calls["upload"] += 1
        return "https://example.test/frame.jpg"

    async def fake_pollinations(prompt, start_frame_url=None, duration=vg.VIDEO_DURATION_SECONDS):
        calls["pollinations"] += 1
        assert start_frame_url == "https://example.test/frame.jpg"
        return b"fallback-video", "wan-fast"

    monkeypatch.setattr(vg, "_check_and_count_limit", lambda chat_id: True)
    monkeypatch.setattr(vg, "download_telegram_image", fake_download)
    monkeypatch.setattr(vg, "generate_hf_zerogpu_video", fake_hf)
    monkeypatch.setattr(vg, "upload_media", fake_upload)
    monkeypatch.setattr(vg, "generate_video", fake_pollinations)

    message = Message()
    asyncio.run(vg.process_animate_photo(message, bot=object()))

    assert calls == {"upload": 1, "pollinations": 1, "sent": 1}
    assert message.status.deleted is True
    assert any("резерв" in text.lower() for text in message.status.edits)
