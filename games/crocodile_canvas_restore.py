"""Restore the active Crocodile bitmap when the Mini App is reopened.

The legacy client treated every Socket.IO connect as if its local canvas were the
source of truth. A freshly reopened WebApp therefore joined with a blank bitmap
and immediately uploaded that blank snapshot, overwriting the server-side frame.

This module keeps same-page reconnect behaviour (local bitmap wins after a
transport drop), but makes the first join restore the persisted server frame
before drawing/snapshot submission is enabled.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from aiohttp import web

from games import crocodile


_INDEX_PATH = Path(crocodile.__file__).resolve().parents[1] / "index.html"
_original_join_room = crocodile.join_room
_configured = False


def _image_data_url(image: bytes) -> str:
    if image.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif image.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    else:
        mime = "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


async def join_room_with_canvas_restore(sid, data):
    """Return the current raster together with the normal join acknowledgement."""
    response = await _original_join_room(sid, data)
    if not isinstance(response, dict) or not response.get("ok"):
        return response

    try:
        _room, _chat_id, session = await crocodile._authorize_socket_room(sid, data)
    except crocodile.WebAppAuthError as exc:
        logging.warning("[socket] failed to re-read joined Crocodile session: %s", exc)
        return {"ok": False, "error": "unauthorized"}

    image = session.get("last_preview_bytes")
    if not isinstance(image, (bytes, bytearray)) or not image:
        image = base64.b64decode(crocodile.BLANK_PNG_B64)

    restored = dict(response)
    restored["image"] = _image_data_url(bytes(image))
    return restored


_STATE_DECLARATION = "  let isDirty = false;\n"
_SOCKET_MARKER = "  // --- SOCKET ---\n"
_OLD_FIRST_JOIN = """      // Publish the actual local canvas after every reconnect. This also repairs
      // a preview that stayed blank while the connection was unavailable.
      isDirty = true;
      sendSnap(true);
"""
_SEND_SNAP_DECLARATION = "  function sendSnap(force = false) {\n"
_BEGIN_STROKE_DECLARATION = "  function beginStroke(clientX, clientY) {\n"
_FINISH_DECLARATION = "  window.finish = () => {\n"

_RESTORE_HELPER = r'''  function restoreServerCanvas(imageData) {
    return new Promise((resolve) => {
      if (!imageData) {
        resolve(false);
        return;
      }

      // Make the bitmap match the current WebView before painting the stored
      // preview. resize() preserves the bitmap on later viewport changes.
      resize();
      const image = new Image();
      image.onload = () => {
        ctx.save();
        ctx.globalAlpha = 1;
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        ctx.restore();
        resetHistory();
        isDirty = false;
        roomReady = true;
        hasJoinedRoom = true;
        resolve(true);
      };
      image.onerror = () => resolve(false);
      image.src = imageData;
    });
  }

'''

_NEW_FIRST_JOIN = r'''      // On a fresh WebApp open the server-side bitmap is authoritative. The old
      // behaviour uploaded this brand-new blank canvas here and erased the round.
      if (!hasJoinedRoom) {
        restoreServerCanvas(response.image).then((restored) => {
          if (!restored) {
            console.error("Failed to restore active canvas");
            showTelegramAlert("Не удалось восстановить текущий рисунок. Открой холст ещё раз.");
            socket.disconnect();
          }
        });
        return;
      }

      // A reconnect inside the same already-restored WebApp is different: the
      // local bitmap may contain strokes made while the transport was down.
      roomReady = true;
      isDirty = true;
      sendSnap(true);
'''


def patch_crocodile_html(source: str) -> str:
    """Inject first-join restore logic into the current Mini App source."""
    replacements = (
        (
            _STATE_DECLARATION,
            _STATE_DECLARATION + "  let hasJoinedRoom = false;\n  let roomReady = false;\n",
        ),
        (_SOCKET_MARKER, _RESTORE_HELPER + _SOCKET_MARKER),
        (_OLD_FIRST_JOIN, _NEW_FIRST_JOIN),
        (
            _SEND_SNAP_DECLARATION,
            _SEND_SNAP_DECLARATION
            + "    if (!roomReady) {\n"
            + "      console.log(\"Skip snap: initial canvas restore pending\");\n"
            + "      return;\n"
            + "    }\n",
        ),
        (
            _BEGIN_STROKE_DECLARATION,
            _BEGIN_STROKE_DECLARATION + "    if (!roomReady) return;\n",
        ),
        (
            _FINISH_DECLARATION,
            _FINISH_DECLARATION
            + "    if (!roomReady) {\n"
            + "      showTelegramAlert(\"Рисунок ещё восстанавливается.\");\n"
            + "      return;\n"
            + "    }\n",
        ),
    )

    patched = source
    for old, new in replacements:
        if patched.count(old) != 1:
            raise RuntimeError(f"unexpected Crocodile HTML marker count: {old[:48]!r}")
        patched = patched.replace(old, new, 1)
    return patched


@web.middleware
async def canvas_restore_middleware(request: web.Request, handler):
    if request.path not in {"/game", "/game/"}:
        return await handler(request)

    try:
        source = _INDEX_PATH.read_text(encoding="utf-8")
        html = patch_crocodile_html(source)
    except Exception:
        logging.exception("[crocodile] failed to prepare reopen-safe Mini App HTML")
        return await handler(request)

    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def configure_crocodile_canvas_restore() -> None:
    """Install socket + HTTP restore hooks before the aiohttp app is started."""
    global _configured
    if _configured:
        return

    crocodile.sio.on("join_room", handler=join_room_with_canvas_restore)
    crocodile.app.middlewares.append(canvas_restore_middleware)
    _configured = True
