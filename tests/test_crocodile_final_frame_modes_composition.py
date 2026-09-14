import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def _image_payload(room: str, image: bytes) -> dict:
    return {
        "room": room,
        "image": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"),
    }


def test_modes_final_frame_delegates_regular_round_then_archives(monkeypatch):
    from games import crocodile_modes as modes

    image = b"regular-final-frame"
    session = {
        "word": "барсук",
        "drawer_id": 1,
        "drawer_name": "Первый",
        "drawer_ids": [1, 2],
        "drawer_names": ["Первый", "Второй"],
        "mode": "duo",
    }
    modes.crocodile.game_sessions["-42"] = session
    calls = []

    async def downstream(sid, data):
        calls.append(("downstream", sid, data))
        return "raw-result"

    async def record(*args):
        calls.append(("archive", args))

    monkeypatch.setattr(modes, "record_drawing", record)
    data = _image_payload("m42", image)

    try:
        result = asyncio.run(
            modes.final_frame_with_modes("sid-regular", data, downstream)
        )
    finally:
        modes.crocodile.game_sessions.pop("-42", None)

    assert result == "raw-result"
    assert calls[0] == ("downstream", "sid-regular", data)
    assert calls[1] == (
        "archive",
        (-42, image, "барсук", ["Первый", "Второй"], "duo"),
    )


def test_modes_final_frame_handles_synthetic_duel_without_delegating(monkeypatch):
    from games import crocodile_modes as modes

    image = b"synthetic-final-frame"
    session = {
        "chat_id": "-42",
        "drawer_id": 222,
        "drawer_name": "Художник",
        "mode": "duel",
        "duel_chat_id": "-42",
        "slot": 1,
    }

    async def authorize(_sid, _data):
        return "m42_d1", "-42:d1", session

    monkeypatch.setattr(modes.crocodile, "_authorize_socket_room", authorize)
    finish_duel = AsyncMock()
    monkeypatch.setattr(modes, "_finish_duel_canvas", finish_duel)
    downstream = AsyncMock(
        side_effect=AssertionError("synthetic final frame must not delegate")
    )
    data = _image_payload("m42_d1", image)

    result = asyncio.run(
        modes.final_frame_with_modes("sid-synthetic", data, downstream)
    )

    assert result is None
    assert session["last_preview_bytes"] == image
    downstream.assert_not_awaited()
    finish_duel.assert_awaited_once_with("-42:d1", session, image)


def test_modes_final_frame_delegates_invalid_room_to_downstream():
    from games import crocodile_modes as modes

    downstream = AsyncMock(return_value="delegated")
    data = {"image": "ignored"}

    result = asyncio.run(
        modes.final_frame_with_modes("sid-invalid", data, downstream)
    )

    assert result == "delegated"
    downstream.assert_awaited_once_with("sid-invalid", data)


def test_modes_final_frame_is_explicitly_composed_before_transitional_ui_layer():
    modes_source = (ROOT / "games" / "crocodile_modes.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "games" / "crocodile_runtime.py").read_text(
        encoding="utf-8"
    )
    ui_source = (ROOT / "games" / "crocodile_ui_enhancements.py").read_text(
        encoding="utf-8"
    )

    assert "_original_final_frame" not in modes_source
    assert 'crocodile.sio.on("final_frame"' not in modes_source
    assert "async def final_frame_with_modes(sid, data, next_handler):" in modes_source
    assert "result = await next_handler(sid, data)" in modes_source

    raw_capture = runtime_source.index("raw_final_frame = crocodile.final_frame")
    modes_install = runtime_source.index("configure_crocodile_modes()")
    composition = runtime_source.index(
        "base_final_frame_handler = _compose_socket_final_frame("
    )
    raw_handler = runtime_source.index("raw_final_frame,", composition)
    modes_wrapper = runtime_source.index("final_frame_with_modes,", raw_handler)
    ui_install = runtime_source.index(
        "configure_crocodile_ui_enhancements(\n"
        "        base_final_frame_handler=base_final_frame_handler,"
    )

    assert raw_capture < modes_install < composition < raw_handler < modes_wrapper < ui_install

    # Transitional owner: the next isolated slice removes this UI hidden handler.
    assert "_original_final_frame_handler" in ui_source
    assert "_original_final_frame_handler = base_final_frame_handler" in ui_source
    assert ui_source.count('crocodile.sio.on("final_frame"') == 1
    assert "def configure_crocodile_ui_enhancements(*, base_final_frame_handler)" in ui_source
