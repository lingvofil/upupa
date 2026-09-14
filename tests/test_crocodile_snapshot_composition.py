import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def test_modes_snapshot_delegates_regular_room_and_callback():
    from games import crocodile_modes as modes

    downstream = AsyncMock(return_value="regular-result")
    callback = AsyncMock()
    data = {"room": "m42", "image": "ignored"}

    result = asyncio.run(
        modes.snapshot_with_modes(
            "sid-regular",
            data,
            downstream,
            callback=callback,
        )
    )

    assert result == "regular-result"
    downstream.assert_awaited_once_with(
        "sid-regular",
        data,
        callback=callback,
    )
    callback.assert_not_awaited()


def test_modes_snapshot_handles_synthetic_room_and_callback(monkeypatch):
    from games import crocodile_modes as modes

    image = b"synthetic-frame"
    session = {
        "chat_id": "-42",
        "drawer_id": 222,
        "drawer_name": "Художник",
        "last_preview_time": 0,
    }

    async def authorize(_sid, _data):
        return "m42_d1", "-42:d1", session

    monkeypatch.setattr(modes.crocodile, "_authorize_socket_room", authorize)
    monkeypatch.setattr(modes.time, "time", lambda: 1234.5)
    downstream = AsyncMock(
        side_effect=AssertionError("synthetic snapshot must not delegate")
    )
    callback = AsyncMock()
    data = {
        "room": "m42_d1",
        "image": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"),
    }

    result = asyncio.run(
        modes.snapshot_with_modes(
            "sid-synthetic",
            data,
            downstream,
            callback=callback,
        )
    )

    assert result == "OK"
    assert session["last_preview_bytes"] == image
    assert session["last_preview_time"] == 1234.5
    downstream.assert_not_awaited()
    callback.assert_awaited_once_with("OK")


def test_runtime_owns_snapshot_registration_and_modes_is_wrapper_only():
    modes_source = (ROOT / "games" / "crocodile_modes.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "_original_snapshot" not in modes_source
    assert 'crocodile.sio.on("snapshot"' not in modes_source
    assert "async def snapshot_with_modes(sid, data, next_handler, callback=None):" in modes_source
    assert "return await next_handler(sid, data, callback=callback)" in modes_source

    raw_capture = runtime_source.index("raw_snapshot = crocodile.snapshot")
    modes_install = runtime_source.index("configure_crocodile_modes()")
    registration = runtime_source.index('crocodile.sio.on(\n        "snapshot",')
    composition = runtime_source.index(
        "handler=_compose_socket_snapshot(raw_snapshot, snapshot_with_modes)",
        registration,
    )

    assert raw_capture < modes_install < registration < composition
    assert runtime_source.count('crocodile.sio.on(\n        "snapshot",') == 1

    # final_frame remains deliberately outside this slice.
    assert "_original_final_frame" in modes_source
    assert 'crocodile.sio.on("final_frame", handler=final_frame_with_modes)' in modes_source
