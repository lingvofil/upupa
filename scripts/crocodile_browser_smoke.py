#!/usr/bin/env python3
"""Hermetic Chromium smoke for the static Crocodile Mini App.

The browser executes the real ``index.html`` while Telegram WebApp and Socket.IO
are replaced at their external script boundaries. No production room, Telegram
message, or remote service is touched.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Route, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "index.html"
TELEGRAM_SDK_URL = "https://telegram.org/js/telegram-web-app.js"
SOCKET_IO_URL = "https://cdn.socket.io/4.7.2/socket.io.min.js"

TELEGRAM_STUB = r"""
(() => {
  const startParam = new URL(window.location.href).searchParams.get("start_param") || "m100";
  window.__smoke = {
    telegramCalls: [],
    emits: [],
    handlers: {},
    joinResponse: null,
    unexpectedHttps: []
  };
  window.Telegram = {
    WebApp: {
      initData: "query_id=browser-smoke&start_param=" + encodeURIComponent(startParam),
      initDataUnsafe: { start_param: startParam },
      expand() { window.__smoke.telegramCalls.push(["expand"]); },
      disableVerticalSwipes() { window.__smoke.telegramCalls.push(["disableVerticalSwipes"]); },
      ready() { window.__smoke.telegramCalls.push(["ready"]); },
      showAlert(message) { window.__smoke.telegramCalls.push(["alert", String(message)]); },
      showPopup(options) {
        window.__smoke.telegramCalls.push(["popup", String((options && options.message) || "")]);
      },
      close() { window.__smoke.telegramCalls.push(["close"]); }
    }
  };
})();
"""

SOCKET_IO_STUB = r"""
(() => {
  window.io = function io(options) {
    const handlers = {};
    const socket = {
      connected: true,
      options,
      io: { engine: { transport: { name: "polling" } } },
      on(name, callback) {
        handlers[name] = callback;
        window.__smoke.handlers[name] = callback;
        return socket;
      },
      emit(name, payload, callback) {
        let storedPayload = payload;
        if (name === "snapshot" && payload && payload.image) {
          storedPayload = {
            room: payload.room,
            imagePrefix: String(payload.image).slice(0, 32)
          };
        }
        window.__smoke.emits.push({ name, payload: storedPayload });
        if (typeof callback === "function") {
          if (name === "join_room") {
            setTimeout(() => callback(window.__smoke.joinResponse), 0);
          } else if (name === "snapshot") {
            setTimeout(() => callback("OK"), 0);
          } else if (name === "submit_text") {
            setTimeout(() => callback({ ok: true }), 0);
          }
        }
        return socket;
      },
      disconnect() {
        socket.connected = false;
        if (handlers.disconnect) handlers.disconnect("io client disconnect");
        return socket;
      }
    };
    window.__smoke.socket = socket;
    window.__smoke.trigger = function trigger(name, argument) {
      if (!handlers[name]) throw new Error("Missing socket handler: " + name);
      return handlers[name](argument);
    };
    return socket;
  };
})();
"""


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _install_external_stubs(page: Page, unexpected_https: list[str]) -> None:
    def route_external(route: Route) -> None:
        url = route.request.url
        if url == TELEGRAM_SDK_URL:
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=TELEGRAM_STUB,
            )
            return
        if url == SOCKET_IO_URL:
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=SOCKET_IO_STUB,
            )
            return
        unexpected_https.append(url)
        route.abort()

    page.route("https://**/*", route_external)


def _open_page(context: BrowserContext, *, start_param: str) -> tuple[Page, list[str], list[str]]:
    page = context.new_page()
    page_errors: list[str] = []
    unexpected_https: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    _install_external_stubs(page, unexpected_https)
    page.goto(
        f"{INDEX_PATH.as_uri()}?start_param={start_param}",
        wait_until="load",
    )
    page.wait_for_timeout(150)
    return page, page_errors, unexpected_https


def _solid_image_data_url(page: Page, color: str) -> str:
    return page.evaluate(
        """
        color => {
          const canvas = document.createElement("canvas");
          canvas.width = 2;
          canvas.height = 2;
          const context = canvas.getContext("2d");
          context.fillStyle = color;
          context.fillRect(0, 0, 2, 2);
          return canvas.toDataURL("image/png");
        }
        """,
        color,
    )


def _event_count(page: Page, event_name: str) -> int:
    return page.evaluate(
        "name => window.__smoke.emits.filter(item => item.name === name).length",
        event_name,
    )


def _pixel(page: Page, x: int = 5, y: int = 5) -> list[int]:
    return page.evaluate(
        """
        ([x, y]) => Array.from(
          document.getElementById("canvas").getContext("2d").getImageData(x, y, 1, 1).data
        )
        """,
        [x, y],
    )


def _assert_clean_page(page_errors: list[str], unexpected_https: list[str]) -> None:
    require(not page_errors, f"Mini App raised browser errors: {page_errors}")
    require(
        not unexpected_https,
        f"Mini App attempted unexpected external requests: {unexpected_https}",
    )


def smoke_draw_and_reconnect(context: BrowserContext) -> None:
    page, page_errors, unexpected_https = _open_page(context, start_param="m100")
    try:
        telegram_calls = page.evaluate("() => window.__smoke.telegramCalls.map(item => item[0])")
        require(
            telegram_calls[:3] == ["expand", "disableVerticalSwipes", "ready"],
            f"Telegram WebApp bootstrap calls changed: {telegram_calls}",
        )
        require(page.locator("#canvas").count() == 1, "Canvas is missing")
        require(page.locator(".swatch").count() >= 14, "Static color palette did not initialize")
        require(page.locator("#eraserButton").count() == 1, "Eraser control is missing")
        require(page.locator("#brushSize").count() == 1, "Brush size control is missing")

        red_image = _solid_image_data_url(page, "#ff0000")
        page.evaluate(
            """
            image => {
              window.__smoke.joinResponse = {
                ok: true,
                ui_mode: "draw",
                image,
                word: "smoke"
              };
              window.__smoke.trigger("connect");
            }
            """,
            red_image,
        )
        page.wait_for_function(
            "() => window.__smoke.telegramCalls.some(item => item[0] === 'popup' && item[1] === 'SMOKE')"
        )
        page.wait_for_timeout(50)

        require(_event_count(page, "join_room") == 1, "First connect did not join the room")
        require(
            _event_count(page, "snapshot") == 0,
            "First connect uploaded a fresh/blank canvas before local interaction",
        )
        initial_corner = _pixel(page)
        require(
            initial_corner[0] > 240 and initial_corner[1] < 15 and initial_corner[2] < 15,
            f"Server canvas was not restored in Chromium: pixel={initial_corner}",
        )

        box = page.locator("#canvas").bounding_box()
        require(box is not None and box["width"] > 100 and box["height"] > 100, "Canvas has no usable browser layout")
        start_x = box["x"] + box["width"] * 0.35
        start_y = box["y"] + box["height"] * 0.35
        end_x = box["x"] + box["width"] * 0.65
        end_y = box["y"] + box["height"] * 0.65
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(end_x, end_y, steps=5)
        page.mouse.up()
        page.wait_for_function(
            "() => window.__smoke.emits.some(item => item.name === 'draw_step') && window.__smoke.emits.some(item => item.name === 'snapshot')"
        )

        require(_event_count(page, "draw_step") > 0, "Browser drawing did not emit draw_step")
        snapshot_before_reconnect = _event_count(page, "snapshot")
        require(snapshot_before_reconnect > 0, "Browser drawing did not publish a snapshot")

        blue_image = _solid_image_data_url(page, "#0000ff")
        page.evaluate(
            """
            image => {
              window.__smoke.joinResponse.image = image;
              window.__smoke.trigger("disconnect");
              window.__smoke.socket.connected = true;
              window.__smoke.trigger("connect");
            }
            """,
            blue_image,
        )
        page.wait_for_function(
            "previous => window.__smoke.emits.filter(item => item.name === 'snapshot').length > previous",
            arg=snapshot_before_reconnect,
        )
        page.wait_for_timeout(100)

        require(_event_count(page, "join_room") == 2, "Reconnect did not re-authorize the room")
        reconnect_corner = _pixel(page)
        require(
            reconnect_corner[0] > 240 and reconnect_corner[1] < 15 and reconnect_corner[2] < 15,
            f"Reconnect overwrote the local canvas with the server frame: pixel={reconnect_corner}",
        )
        _assert_clean_page(page_errors, unexpected_https)
    finally:
        page.close()


def smoke_text_reconnect(context: BrowserContext) -> None:
    page, page_errors, unexpected_https = _open_page(context, start_param="m100_t1")
    try:
        page.evaluate(
            """
            () => {
              window.__smoke.joinResponse = {
                ok: true,
                ui_mode: "text",
                prompt: "Что было на рисунке?"
              };
              window.__smoke.trigger("connect");
            }
            """
        )
        page.wait_for_selector("#telephoneTextPanel textarea", state="visible")
        require(page.locator("#telephoneTextPanel").count() == 1, "Telephone text panel was not created")
        require(page.locator("#canvas").evaluate("element => element.style.display") == "none", "Canvas stayed visible in text mode")
        require(_event_count(page, "snapshot") == 0, "Text mode published a hidden canvas snapshot")

        page.evaluate(
            """
            () => {
              window.__smoke.trigger("disconnect");
              window.__smoke.socket.connected = true;
              window.__smoke.trigger("connect");
            }
            """
        )
        page.wait_for_function(
            "() => window.__smoke.emits.filter(item => item.name === 'join_room').length === 2"
        )
        page.wait_for_timeout(50)
        require(page.locator("#telephoneTextPanel").count() == 1, "Text reconnect duplicated the input panel")
        require(_event_count(page, "snapshot") == 0, "Text reconnect published a hidden canvas snapshot")

        page.locator("#telephoneTextPanel textarea").fill("тестовый ответ")
        page.locator("#telephoneTextPanel button").click()
        page.wait_for_function(
            "() => window.__smoke.emits.some(item => item.name === 'submit_text')"
        )
        page.wait_for_function(
            "() => window.__smoke.telegramCalls.some(item => item[0] === 'close')"
        )
        submit = page.evaluate(
            "() => window.__smoke.emits.find(item => item.name === 'submit_text').payload"
        )
        require(submit["room"] == "m100_t1", f"Text submit used wrong room: {submit}")
        require(submit["text"] == "тестовый ответ", f"Text submit changed user input: {submit}")
        _assert_clean_page(page_errors, unexpected_https)
    finally:
        page.close()


def main() -> int:
    require(INDEX_PATH.is_file(), f"Mini App HTML not found: {INDEX_PATH}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844})
            try:
                smoke_draw_and_reconnect(context)
                smoke_text_reconnect(context)
            finally:
                context.close()
        finally:
            browser.close()

    print("crocodile browser smoke: draw/restore/reconnect/text mode ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
