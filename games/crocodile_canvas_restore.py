"""Restore Crocodile canvases through explicit Socket.IO composition."""

from __future__ import annotations

import base64
import logging

from games import crocodile




def _image_data_url(image: bytes) -> str:
    if image.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif image.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    else:
        mime = "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


async def join_room_with_canvas_restore(sid, data, next_handler):
    """Return current raster plus the UI contract for the authorized step."""
    response = await next_handler(sid, data)
    if not isinstance(response, dict) or not response.get("ok"):
        return response

    try:
        _room, _session_key, session = await crocodile._authorize_socket_room(sid, data)
    except crocodile.WebAppAuthError as exc:
        logging.warning("[socket] failed to re-read joined Crocodile session: %s", exc)
        return {"ok": False, "error": "unauthorized"}

    image = session.get("last_preview_bytes")
    if not isinstance(image, (bytes, bytearray)) or not image:
        image = base64.b64decode(crocodile.BLANK_PNG_B64)

    restored = dict(response)
    restored["image"] = _image_data_url(bytes(image))
    try:
        from games.crocodile_modes import canvas_join_payload

        restored.update(canvas_join_payload(session))
    except Exception:
        restored.setdefault("ui_mode", "draw")
        restored.setdefault("word", session.get("word", ""))
    return restored
