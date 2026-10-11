"""Tests for Kandinsky 6.0 video via HF ZeroGPU Spaces and its place in the chain."""

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image

from tests import test_smoke_imports  # noqa: F401  (env + heavy-lib mocks)
from AI import hf_kandinsky_video as kv
from AI import videogeneration as vg


def _jpeg_bytes():
    out = BytesIO()
    Image.new("RGB", (640, 480), "white").save(out, format="JPEG")
    return out.getvalue()


class _FakeJob:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def result(self, timeout=None):
        if self._error:
            raise self._error
        return self._result


def _install_fake_client(monkeypatch, behaviour, calls):
    class FakeClient:
        def __init__(self, space_id, token=None, verbose=False, download_files=None):
            self.space_id = space_id
            self.download_files = download_files

        def submit(self, api_name=None, **kwargs):
            calls.append((self.space_id, api_name, kwargs))
            return behaviour(self, kwargs)

        def close(self):
            pass

    monkeypatch.setattr(kv, "Client", FakeClient)
    monkeypatch.setattr(kv, "handle_file", lambda path: {"path": path})


def test_lite_space_image_to_video(monkeypatch):
    calls = []

    def behaviour(client, kwargs):
        out = Path(client.download_files) / "video.mp4"
        out.write_bytes(b"k6-video")
        return _FakeJob(result=(str(out), "prompt", "info", "stats"))

    _install_fake_client(monkeypatch, behaviour, calls)
    video, status = asyncio.run(kv.generate_kandinsky_video(_jpeg_bytes(), "улыбнись"))

    assert (video, status) == (b"k6-video", "ok")
    assert len(calls) == 1
    space, api_name, kwargs = calls[0]
    assert space == kv.KANDINSKY_VIDEO_SPACES[0][0]
    assert api_name == "/generate"
    assert kwargs["prompt"] == "улыбнись"
    assert kwargs["image"]["path"].endswith("input.jpg")
    assert kwargs["expand_prompt"] is False


def test_text_to_video_sends_no_image(monkeypatch):
    calls = []

    def behaviour(client, kwargs):
        out = Path(client.download_files) / "video.mp4"
        out.write_bytes(b"t2v")
        return _FakeJob(result={"video": str(out)})

    _install_fake_client(monkeypatch, behaviour, calls)
    video, status = asyncio.run(kv.generate_kandinsky_video(None, "кот на пляже"))

    assert (video, status) == (b"t2v", "ok")
    assert "image" not in calls[0][2]


def test_falls_through_to_pro_space_when_lite_is_down(monkeypatch):
    calls = []

    def behaviour(client, kwargs):
        if client.space_id == kv.KANDINSKY_VIDEO_SPACES[0][0]:
            return _FakeJob(error=RuntimeError("Space is sleeping"))
        out = Path(client.download_files) / "pro.mp4"
        out.write_bytes(b"pro-video")
        return _FakeJob(result=str(out))

    _install_fake_client(monkeypatch, behaviour, calls)
    video, status = asyncio.run(kv.generate_kandinsky_video(_jpeg_bytes(), "move"))

    assert (video, status) == (b"pro-video", "ok")
    assert [c[0] for c in calls] == [s for s, _ in kv.KANDINSKY_VIDEO_SPACES]
    assert calls[1][2]["use_beautifier"] is True


def test_quota_stops_waterfall(monkeypatch):
    calls = []

    def behaviour(client, kwargs):
        return _FakeJob(error=RuntimeError("You have exceeded your free ZeroGPU quota"))

    _install_fake_client(monkeypatch, behaviour, calls)
    video, status = asyncio.run(kv.generate_kandinsky_video(_jpeg_bytes(), "move"))

    assert (video, status) == (None, "quota")
    assert len(calls) == 1, "Pro Space дороже по квоте — после quota на Lite его не дёргаем"


class _Status:
    def __init__(self):
        self.edits = []
        self.deleted = False

    async def edit_text(self, text):
        self.edits.append(text)

    async def delete(self):
        self.deleted = True


def _message(text, photo=True, reply_photo=None):
    sent = []

    class Message:
        caption = None
        reply_to_message = None

        class Chat:
            id = 777

        chat = Chat()

        def __init__(self):
            self.text = text
            self.photo = [object()] if photo else None
            self.status = _Status()
            if reply_photo is not None:
                self.reply_to_message = type("R", (), {"photo": reply_photo})()

        async def reply(self, _text):
            return self.status

        async def reply_video(self, video):
            sent.append(video)

    return Message(), sent


def test_animate_tries_kandinsky_first(monkeypatch):
    async def fake_download(bot, photo):
        return b"telegram-image"

    async def fake_kandinsky(image, prompt):
        assert image == b"telegram-image"
        return b"k-video", "ok"

    async def forbidden(*args, **kwargs):
        raise AssertionError("fallbacks must not run when Kandinsky succeeds")

    monkeypatch.setattr(vg, "_check_and_count_limit", lambda chat_id: True)
    monkeypatch.setattr(vg, "download_telegram_image", fake_download)
    monkeypatch.setattr(vg, "generate_kandinsky_video", fake_kandinsky)
    monkeypatch.setattr(vg, "generate_hf_zerogpu_video", forbidden)
    monkeypatch.setattr(vg, "upload_media", forbidden)

    message, sent = _message("оживи")
    asyncio.run(vg.process_animate_photo(message, bot=object()))

    assert len(sent) == 1
    assert message.status.deleted is True


def test_shoot_text_to_video_uses_kandinsky_first(monkeypatch):
    async def fake_kandinsky(image, prompt):
        assert image is None
        assert prompt == "кота в космосе"
        return b"k-video", "ok"

    async def forbidden(*args, **kwargs):
        raise AssertionError("Pollinations must not run when Kandinsky succeeds")

    monkeypatch.setattr(vg, "_check_and_count_limit", lambda chat_id: True)
    monkeypatch.setattr(vg, "generate_kandinsky_video", fake_kandinsky)
    monkeypatch.setattr(vg, "generate_video", forbidden)
    monkeypatch.setattr(vg, "upload_media", forbidden)

    message, sent = _message("упупа сними кота в космосе", photo=False)
    asyncio.run(vg.process_video_generation(message, bot=object()))

    assert len(sent) == 1
    assert message.status.deleted is True


def test_shoot_falls_back_to_pollinations_with_reply_frame(monkeypatch):
    calls = {}

    async def fake_download(bot, photo):
        return b"frame"

    async def fake_kandinsky(image, prompt):
        calls["k_image"] = image
        return None, "quota"

    async def fake_upload(image):
        calls["uploaded"] = image
        return "https://example.test/frame.jpg"

    async def fake_pollinations(prompt, start_frame_url=None, duration=vg.VIDEO_DURATION_SECONDS):
        calls["start"] = start_frame_url
        return b"p-video", "wan-fast"

    monkeypatch.setattr(vg, "_check_and_count_limit", lambda chat_id: True)
    monkeypatch.setattr(vg, "download_telegram_image", fake_download)
    monkeypatch.setattr(vg, "generate_kandinsky_video", fake_kandinsky)
    monkeypatch.setattr(vg, "upload_media", fake_upload)
    monkeypatch.setattr(vg, "generate_video", fake_pollinations)

    message, sent = _message("упупа сними взрыв", photo=False, reply_photo=[object()])
    asyncio.run(vg.process_video_generation(message, bot=object()))

    assert calls == {"k_image": b"frame", "uploaded": b"frame", "start": "https://example.test/frame.jpg"}
    assert len(sent) == 1
    assert any("резерв" in t.lower() for t in message.status.edits)
