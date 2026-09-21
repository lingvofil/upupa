import asyncio
import base64
from pathlib import Path
import shutil
import subprocess

import pytest

from tests import test_smoke_imports  # noqa: F401
from games import crocodile
from games import crocodile_canvas_restore as restore


ROOT = Path(__file__).resolve().parents[1]


def test_join_response_contains_current_server_bitmap(monkeypatch):
    image = b"\xff\xd8saved-jpeg"

    async def original_join(_sid, _data):
        return {"ok": True}

    async def authorize(_sid, _data):
        return "m100", "-100", {"last_preview_bytes": image}

    monkeypatch.setattr(crocodile, "_authorize_socket_room", authorize)

    result = asyncio.run(
        restore.join_room_with_canvas_restore(
            "sid",
            {"room": "m100"},
            original_join,
        )
    )

    assert result["ok"] is True
    assert result["image"].startswith("data:image/jpeg;base64,")
    encoded = result["image"].split(",", 1)[1]
    assert base64.b64decode(encoded) == image


def test_failed_join_is_returned_without_exposing_bitmap(monkeypatch):
    async def original_join(_sid, _data):
        return {"ok": False, "error": "unauthorized"}

    async def authorize(_sid, _data):
        raise AssertionError("authorization must not be repeated for failed join")

    monkeypatch.setattr(crocodile, "_authorize_socket_room", authorize)

    assert asyncio.run(
        restore.join_room_with_canvas_restore(
            "sid",
            {"room": "m100"},
            original_join,
        )
    ) == {"ok": False, "error": "unauthorized"}


def test_join_room_restore_is_explicitly_composed_in_runtime():
    restore_source = (ROOT / "games" / "crocodile_canvas_restore.py").read_text(
        encoding="utf-8"
    )
    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "_original_join_room" not in restore_source
    assert "patch_crocodile_html" not in restore_source
    assert "canvas_restore_middleware" not in restore_source
    assert "crocodile.sio.on(" not in restore_source
    assert "middlewares.append" not in restore_source
    assert (
        "async def join_room_with_canvas_restore(sid, data, next_handler)"
        in restore_source
    )
    assert "response = await next_handler(sid, data)" in restore_source

    capture = runtime_source.index("raw_join_room = crocodile.join_room")
    assignment = runtime_source.index(
        'crocodile.sio.on(\n        "join_room",',
        capture,
    )
    wrapper = runtime_source.index("join_room_with_canvas_restore,", assignment)

    assert "_configured = False" not in restore_source
    assert "def configure_crocodile_canvas_restore(" not in restore_source
    assert "configure_crocodile_canvas_restore(" not in runtime_source
    assert runtime_source.count('"join_room",') == 1
    assert capture < assignment < wrapper


def test_mini_app_first_join_restores_server_frame_before_snapshots():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "let hasJoinedRoom = false;" in source
    assert "let roomReady = false;" in source
    assert 'let activeUiMode = "draw";' in source
    assert "restoreServerCanvas(response.image)" in source
    assert 'if (!roomReady || activeUiMode !== "draw") {' in source
    assert "Рисунок ещё восстанавливается." in source

    connect_pos = source.index('socket.on("connect", () => {')
    first_restore = source.index("restoreServerCanvas(response.image)", connect_pos)
    reconnect = source.index(
        "A reconnect inside the same already-restored WebApp",
        first_restore,
    )
    reconnect_snap = source.index("sendSnap(true);", reconnect)

    assert connect_pos < first_restore < reconnect < reconnect_snap


def test_mini_app_text_reconnect_never_publishes_hidden_canvas():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    text_mode = source.index('if (nextUiMode === "text") {')
    draw_mode = source.index('activeUiMode = "draw";', text_mode)
    text_block = source[text_mode:draw_mode]

    assert (
        '!hasJoinedRoom || !document.getElementById("telephoneTextPanel")'
        in text_block
    )
    assert "activateTelephoneTextMode(response);" in text_block
    assert "sendSnap(true);" not in text_block

    disconnect = source.index('socket.on("disconnect", () => {')
    connect_error = source.index('socket.on("connect_error",', disconnect)
    disconnect_block = source[disconnect:connect_error]
    assert 'if (activeUiMode === "draw") isDirty = true;' in disconnect_block


def test_mini_app_restore_logic_is_static_and_javascript_parses(tmp_path):
    source = (ROOT / "index.html").read_text(encoding="utf-8")
    restore_source = (ROOT / "games" / "crocodile_canvas_restore.py").read_text(
        encoding="utf-8"
    )

    assert "function restoreServerCanvas(imageData)" in source
    assert "function activateTelephoneTextMode(response)" in source
    assert "function configureTurnFinishButton()" in source
    assert "patch_crocodile_html" not in restore_source
    assert "_INDEX_PATH" not in restore_source

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available for Mini App JavaScript syntax smoke")

    script = source.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    script_path = tmp_path / "crocodile-mini-app.js"
    script_path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_bootstrap_configures_runtime_before_session_restore_and_socket_start():
    source = (ROOT / "app" / "bootstrap.py").read_text(encoding="utf-8")

    configure_pos = source.index("configure_crocodile_runtime()")
    restore_pos = source.index("restore_crocodile_sessions()")
    socket_pos = source.rindex("crocodile_socket_server_loop")

    assert configure_pos < restore_pos < socket_pos
