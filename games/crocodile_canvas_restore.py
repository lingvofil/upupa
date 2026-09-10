"""Restore Crocodile canvases and inject mode-aware Mini App UI."""

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
    """Return current raster plus the UI contract for the authorized step."""
    response = await _original_join_room(sid, data)
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

_RESTORE_HELPER = r'''  function activateTelephoneTextMode(response) {
    roomReady = true;
    hasJoinedRoom = true;
    canvas.style.display = "none";
    const toolbar = document.getElementById("toolbar");
    if (toolbar) toolbar.style.display = "none";

    let panel = document.getElementById("telephoneTextPanel");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "telephoneTextPanel";
      panel.style.cssText = "min-height:100vh;background:#222;color:#fff;padding:20px;display:flex;flex-direction:column;gap:14px;font-family:system-ui,-apple-system,sans-serif;overflow:auto";
      document.body.appendChild(panel);
    }
    panel.innerHTML = "";

    const title = document.createElement("div");
    title.textContent = response.prompt || "Напиши ответ";
    title.style.cssText = "font-size:20px;font-weight:800;line-height:1.3";
    panel.appendChild(title);

    if (response.reference_image) {
      const img = document.createElement("img");
      img.src = response.reference_image;
      img.alt = "Предыдущий рисунок";
      img.style.cssText = "width:100%;max-height:58vh;object-fit:contain;background:#fff;border-radius:14px";
      panel.appendChild(img);
    }

    const input = document.createElement("textarea");
    input.maxLength = 120;
    input.rows = 3;
    input.placeholder = "Пиши сюда…";
    input.style.cssText = "width:100%;font-size:18px;padding:14px;border-radius:12px;border:0;resize:none;user-select:text;-webkit-user-select:text";
    panel.appendChild(input);

    const submit = document.createElement("button");
    submit.textContent = "Готово ✓";
    submit.style.cssText = "font-size:18px;font-weight:800;padding:14px;border:0;border-radius:12px;background:#34c759;color:#fff";
    panel.appendChild(submit);
    submit.onclick = () => {
      const text = input.value.trim();
      if (!text) {
        showTelegramAlert("Сначала что-нибудь напиши.");
        return;
      }
      submit.disabled = true;
      socket.emit("submit_text", { room: roomId, text: text }, (result) => {
        if (result && result.ok) {
          closeWebApp();
          return;
        }
        submit.disabled = false;
        showTelegramAlert((result && result.error) || "Не удалось отправить ответ.");
      });
    };
    setTimeout(() => input.focus(), 100);
  }

  function restoreServerCanvas(imageData) {
    return new Promise((resolve) => {
      if (!imageData) {
        resolve(false);
        return;
      }

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

_NEW_FIRST_JOIN = r'''      // On a fresh WebApp open the server-side state is authoritative.
      if (!hasJoinedRoom) {
        if (response.ui_mode === "text") {
          activateTelephoneTextMode(response);
          return;
        }
        restoreServerCanvas(response.image).then((restored) => {
          if (!restored) {
            console.error("Failed to restore active canvas");
            showTelegramAlert("Не удалось восстановить текущий рисунок. Открой холст ещё раз.");
            socket.disconnect();
            return;
          }
          if (response.word) showWordPopup(response.word);
        });
        return;
      }

      // A reconnect inside the same already-restored WebApp keeps local strokes.
      roomReady = true;
      isDirty = true;
      sendSnap(true);
'''


def patch_crocodile_html(source: str) -> str:
    """Inject first-join restore and telephone text-step UI."""
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
        html_source = patch_crocodile_html(source)
    except Exception:
        logging.exception("[crocodile] failed to prepare reopen-safe Mini App HTML")
        return await handler(request)
    return web.Response(
        text=html_source,
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def configure_crocodile_canvas_restore() -> None:
    """Install party modes, socket restore and HTTP patch before server start."""
    global _configured
    if _configured:
        return
    from games.crocodile_admin_controls import configure_crocodile_admin_controls
    from games.crocodile_duo_optin import configure_crocodile_duo_opt_in
    from games.crocodile_modes import configure_crocodile_modes
    from games.crocodile_party_controls import configure_crocodile_party_controls
    from games.crocodile_party_state import configure_crocodile_party_state

    # Bootstrap calls this after persistence/controls, so this is the stable
    # composition point for the extra Socket.IO handlers and mode persistence.
    configure_crocodile_modes()
    configure_crocodile_party_state()
    configure_crocodile_party_controls()
    configure_crocodile_duo_opt_in()
    configure_crocodile_admin_controls()
    crocodile.sio.on("join_room", handler=join_room_with_canvas_restore)
    crocodile.app.middlewares.append(canvas_restore_middleware)
    _configured = True
