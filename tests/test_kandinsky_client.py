import base64
from types import SimpleNamespace

from AI import kandinsky_client as kc


class FakeResponse:
    def __init__(self, status_code=200, data=None, text=None, content=b""):
        self.status_code = status_code
        self._data = data
        self.text = text if text is not None else ("" if data is None else str(data))
        self.content = content

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def test_get_pipeline_requests_text2image_and_skips_disabled(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("key/api/v1/pipelines"):
            return FakeResponse(data=[
                {
                    "id": "old-disabled",
                    "name": "old",
                    "type": "TEXT2IMAGE",
                    "status": "DISABLED_BY_QUEUE",
                    "version": 3.0,
                },
                {
                    "id": "active-pipeline",
                    "name": "Kandinsky",
                    "type": "TEXT2IMAGE",
                    "status": "ACTIVE",
                    "version": 4.1,
                },
            ])
        if url.endswith("key/api/v1/pipeline/active-pipeline/availability"):
            return FakeResponse(data={"status": "ACTIVE"})
        raise AssertionError(url)

    monkeypatch.setattr(kc.requests, "get", fake_get)

    client = kc.FusionBrainAPI("https://api-key.fusionbrain.ai/", "key", "secret")
    assert client.get_pipeline() == "active-pipeline"

    pipeline_call = next(call for call in calls if call[0].endswith("/pipelines"))
    assert pipeline_call[1]["params"] == {"type": "TEXT2IMAGE"}


def test_generate_refreshes_stale_pipeline_and_remembers_status_time(monkeypatch):
    posted = {}

    def fake_get(url, **kwargs):
        if url.endswith("key/api/v1/pipeline/stale/availability"):
            return FakeResponse(data={"status": "DISABLED_BY_QUEUE"})
        if url.endswith("key/api/v1/pipelines"):
            return FakeResponse(data=[{
                "id": "fresh",
                "name": "new",
                "type": "TEXT2IMAGE",
                "status": "ACTIVE",
                "version": 5.0,
            }])
        if url.endswith("key/api/v1/pipeline/fresh/availability"):
            return FakeResponse(data={"status": "ACTIVE"})
        raise AssertionError(url)

    def fake_post(url, **kwargs):
        posted.update(kwargs["files"])
        return FakeResponse(
            status_code=201,
            data={"uuid": "request-1", "status": "INITIAL", "status_time": 7},
        )

    monkeypatch.setattr(kc.requests, "get", fake_get)
    monkeypatch.setattr(kc.requests, "post", fake_post)

    client = kc.FusionBrainAPI("https://api-key.fusionbrain.ai/", "key", "secret")
    uuid, error = client.generate("нарисуй кота", "stale")

    assert error is None
    assert uuid == "request-1"
    assert posted["pipeline_id"][1] == "fresh"
    assert client._initial_delays["request-1"] == 7


def test_generate_reports_model_status_instead_of_silent_empty_uuid(monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("key/api/v1/pipeline/p1/availability"):
            return FakeResponse(data={"status": "ACTIVE"})
        if url.endswith("key/api/v1/pipelines"):
            return FakeResponse(data=[{
                "id": "p1",
                "name": "Kandinsky",
                "type": "TEXT2IMAGE",
                "status": "ACTIVE",
                "version": 4.1,
            }])
        raise AssertionError(url)

    def fake_post(url, **kwargs):
        return FakeResponse(status_code=201, data={"model_status": "DISABLED_BY_QUEUE"})

    monkeypatch.setattr(kc.requests, "get", fake_get)
    monkeypatch.setattr(kc.requests, "post", fake_post)

    client = kc.FusionBrainAPI("https://api-key.fusionbrain.ai/", "key", "secret")
    uuid, error = client.generate("cat", "p1")

    assert uuid is None
    assert error == "model_status=DISABLED_BY_QUEUE"


def test_check_uses_status_time_and_waits_until_done(monkeypatch):
    image_bytes = b"fake-png-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    statuses = iter([
        FakeResponse(data={"uuid": "u1", "status": "PROCESSING", "result": None}),
        FakeResponse(data={
            "uuid": "u1",
            "status": "DONE",
            "result": {"files": [encoded], "censored": False},
            "generationTime": 1234,
        }),
    ])
    sleeps = []

    def fake_get(url, **kwargs):
        assert url.endswith("key/api/v1/pipeline/status/u1")
        return next(statuses)

    monkeypatch.setattr(kc.requests, "get", fake_get)
    monkeypatch.setattr(kc.time, "sleep", lambda seconds: sleeps.append(seconds))

    client = kc.FusionBrainAPI(
        "https://api-key.fusionbrain.ai/",
        "key",
        "secret",
        poll_interval=0.25,
        max_wait_seconds=10,
    )
    client._initial_delays["u1"] = 4

    image, error = client.check("u1")

    assert error is None
    assert image == image_bytes
    assert sleeps == [4, 0.25]


def test_install_replaces_old_client_and_drops_cached_pipeline():
    module = SimpleNamespace(
        KANDINSKY_API_KEY="key",
        KANDINSKY_SECRET_KEY="secret",
        PIPELINE_ID="stale",
        kandinsky_api=object(),
    )

    client = kc.install_into_picgeneration(module)

    assert isinstance(client, kc.FusionBrainAPI)
    assert module.kandinsky_api is client
    assert module.PIPELINE_ID is None
