"""Tests for the AI Horde text-to-image reserve."""

import base64

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)
from AI import aihorde_image as horde


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, content=b"", text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_horde_submit_poll_and_download(monkeypatch):
    image = b"x" * 1200
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(("post", url, headers, json, timeout))
        return FakeResponse(status_code=202, payload={"id": "job-123"})

    def fake_get(url, *, headers=None, timeout=None):
        calls.append(("get", url, headers, timeout))
        if "/generate/check/" in url:
            return FakeResponse(payload={"done": True, "faulted": False})
        if "/generate/status/" in url:
            return FakeResponse(
                payload={
                    "generations": [
                        {
                            "img": "https://images.example/result.webp",
                            "model": "Deliberate 3.0",
                            "worker_name": "worker",
                        }
                    ]
                }
            )
        assert url == "https://images.example/result.webp"
        return FakeResponse(content=image)

    monkeypatch.setattr(horde.requests, "post", fake_post)
    monkeypatch.setattr(horde.requests, "get", fake_get)
    monkeypatch.setattr(horde, "AIHORDE_API_KEY", "0000000000")

    result = horde._generate_aihorde_image_sync("a hoopoe with a tiny sword")

    assert result == image
    submit = calls[0]
    assert submit[0] == "post"
    assert submit[2]["apikey"] == "0000000000"
    assert submit[3]["models"] == ["Deliberate 3.0"]
    assert submit[3]["prompt"].startswith("a hoopoe with a tiny sword ###")
    assert any("/generate/check/job-123" in call[1] for call in calls)
    assert any("/generate/status/job-123" in call[1] for call in calls)


def test_horde_decodes_base64_generation():
    image = b"z" * 1200
    encoded = base64.b64encode(image).decode("ascii")

    assert horde._decode_generation_image(encoded) == image


def test_horde_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        horde.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(status_code=503, text="unavailable"),
    )

    assert horde._generate_aihorde_image_sync("test") is None
