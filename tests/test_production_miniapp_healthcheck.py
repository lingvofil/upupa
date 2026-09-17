from urllib.error import URLError

import pytest

from scripts import production_healthcheck as healthcheck


VALID_HTML = b"""<!DOCTYPE html>
<html>
<head>
  <title>Crocodile</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
</head>
<body><canvas id="canvas"></canvas></body>
</html>
"""
VALID_HANDSHAKE = (
    b'0{"sid":"smoke-session","upgrades":["websocket"],'
    b'"pingTimeout":60000,"pingInterval":25000}'
)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def _opener_for(*, html=VALID_HTML, handshake=VALID_HANDSHAKE, seen=None):
    def opener(request, timeout):
        assert timeout == 3.0
        url = request.full_url
        if seen is not None:
            seen.append(url)
        if url.endswith("/game"):
            return FakeResponse(html)
        if "/socket.io/?" in url:
            return FakeResponse(handshake)
        raise AssertionError(f"unexpected healthcheck URL: {url}")

    return opener


def test_crocodile_mini_app_healthcheck_checks_page_and_engineio_handshake():
    seen = []

    result = healthcheck.check_crocodile_mini_app(
        timeout=3.0,
        base_url="http://127.0.0.1:8080/",
        opener=_opener_for(seen=seen),
    )

    assert result["sid"] == "smoke-session"
    assert seen == [
        "http://127.0.0.1:8080/game",
        "http://127.0.0.1:8080/socket.io/?EIO=4&transport=polling",
    ]


def test_crocodile_mini_app_healthcheck_rejects_wrong_page_before_handshake():
    seen = []

    with pytest.raises(
        healthcheck.HealthCheckError,
        match="Crocodile Mini App returned invalid HTML",
    ):
        healthcheck.check_crocodile_mini_app(
            timeout=3.0,
            opener=_opener_for(html=b"<html><title>wrong</title></html>", seen=seen),
        )

    assert seen == ["http://127.0.0.1:8080/game"]


@pytest.mark.parametrize(
    "handshake",
    [
        b'4{"message":"Forbidden"}',
        b"0not-json",
        b'0{"sid":"","pingTimeout":60000,"pingInterval":25000}',
        b'0{"sid":"smoke","pingTimeout":0,"pingInterval":25000}',
    ],
)
def test_crocodile_mini_app_healthcheck_rejects_invalid_engineio_handshake(handshake):
    with pytest.raises(
        healthcheck.HealthCheckError,
        match="Crocodile Socket.IO handshake is unavailable or invalid",
    ):
        healthcheck.check_crocodile_mini_app(
            timeout=3.0,
            opener=_opener_for(handshake=handshake),
        )


def test_crocodile_mini_app_healthcheck_reports_unreachable_listener():
    def opener(request, timeout):
        raise URLError("connection refused")

    with pytest.raises(
        healthcheck.HealthCheckError,
        match="Crocodile Mini App is unreachable",
    ):
        healthcheck.check_crocodile_mini_app(timeout=3.0, opener=opener)
