from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"
CROCODILE_PY = ROOT / "games" / "crocodile.py"


def _source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _toolbar(source: str) -> str:
    toolbar = source.split('<div id="toolbar" class="toolbar">', 1)[1]
    return toolbar.split("<script>", 1)[0]


def test_crocodile_palette_is_rendered_without_javascript_bootstrap():
    source = _source()
    toolbar = _toolbar(source)

    assert toolbar.count('class="swatch') == 14
    assert 'id="colorPicker"' in toolbar
    assert 'data-c="#000000"' in toolbar
    assert 'data-c="#ff2d55"' in toolbar


def test_crocodile_webapp_tolerates_partial_telegram_api():
    source = _source()

    assert "window.Telegram && window.Telegram.WebApp" in source
    assert 'typeof tg.expand === "function"' in source
    assert 'typeof tg.ready === "function"' in source
    assert 'getHashParam("tgWebAppData")' in source
    assert "showTelegramAlert" in source
    assert "showWordPopup" in source
    assert "closeWebApp" in source


def test_crocodile_canvas_has_eraser_and_brush_size_control_without_skip_button():
    source = _source()
    toolbar = _toolbar(source)

    assert 'id="eraserButton"' in toolbar
    assert '>⌫ Ластик</button>' in toolbar
    assert 'id="brushSize"' in toolbar
    assert 'type="range"' in toolbar
    assert 'min="2"' in toolbar
    assert 'max="28"' in toolbar
    assert 'onclick="skip()"' not in toolbar


def test_crocodile_pointer_coordinates_are_scaled_to_canvas_bitmap():
    source = _source()

    assert "canvas.getBoundingClientRect()" in source
    assert "canvas.width / r.width" in source
    assert "canvas.height / r.height" in source
    assert "(clientX - r.left) * scaleX" in source
    assert "(clientY - r.top) * scaleY" in source


def test_crocodile_eraser_uses_white_without_forgetting_selected_color():
    source = _source()

    assert 'return erasing ? "#ffffff" : currentColor;' in source
    assert "setEraser(false);" in source
    assert 'eraserButton.addEventListener("click"' in source


def test_crocodile_draw_steps_include_brush_width():
    source = _source()
    server_source = CROCODILE_PY.read_text(encoding="utf-8")

    assert "width: width" in source
    assert "Number(d.width) || 6" in source
    assert '("px", "py", "x", "y", "color", "width")' in server_source


def test_crocodile_socket_has_mobile_transport_fallback_and_snapshot_retry():
    source = _source()

    assert 'transports: ["polling", "websocket"]' in source
    assert "upgrade: true" in source
    assert "rememberUpgrade: false" in source
    assert "reconnectionAttempts: Infinity" in source
    assert 'if (response !== "OK")' in source
    assert "Snap timeout - forcing retry" in source


def test_crocodile_has_touch_and_mouse_fallback_for_old_webviews():
    source = _source()

    assert 'typeof window.PointerEvent === "function"' in source
    assert 'canvas.addEventListener("touchstart"' in source
    assert 'canvas.addEventListener("touchmove"' in source
    assert 'canvas.addEventListener("mousedown"' in source
    assert 'window.addEventListener("mouseup"' in source
    assert 'canvas.addEventListener("lostpointercapture"' in source
