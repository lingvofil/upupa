import asyncio
import base64
from pathlib import Path

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

    monkeypatch.setattr(restore, "_original_join_room", original_join)
    monkeypatch.setattr(crocodile, "_authorize_socket_room", authorize)

    result = asyncio.run(
        restore.join_room_with_canvas_restore("sid", {"room": "m100"})
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

    monkeypatch.setattr(restore, "_original_join_room", original_join)
    monkeypatch.setattr(crocodile, "_authorize_socket_room", authorize)

    assert asyncio.run(
        restore.join_room_with_canvas_restore("sid", {"room": "m100"})
    ) == {"ok": False, "error": "unauthorized"}


def test_mini_app_first_join_restores_server_frame_before_snapshots():
    source = (ROOT / "index.html").read_text(encoding="utf-8")
    patched = restore.patch_crocodile_html(source)

    assert "let hasJoinedRoom = false;" in patched
    assert "let roomReady = false;" in patched
    assert "restoreServerCanvas(response.image)" in patched
    assert "Skip snap: initial canvas restore pending" in patched
    assert "if (!roomReady) return;" in patched
    assert "Рисунок ещё восстанавливается." in patched

    # The first WebApp join must no longer upload its freshly-created white
    # bitmap. Same-page transport reconnect still republishes the local bitmap.
    assert "Publish the actual local canvas after every reconnect" not in patched
    reconnect_block = patched.split(
        "A reconnect inside the same already-restored WebApp", 1
    )[1]
    assert "isDirty = true;" in reconnect_block
    assert "sendSnap(true);" in reconnect_block


def test_bootstrap_installs_restore_before_session_restore_and_socket_start():
    source = (ROOT / "app" / "bootstrap.py").read_text(encoding="utf-8")

    configure_pos = source.index("configure_crocodile_canvas_restore()")
    restore_pos = source.index("restore_crocodile_sessions()")
    socket_pos = source.index("crocodile.start_socket_server()")

    assert configure_pos < restore_pos < socket_pos
