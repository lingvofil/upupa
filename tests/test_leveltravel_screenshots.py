import asyncio

from AI import leveltravel, leveltravel_screenshots


class _FakePage:
    def __init__(self):
        self.goto_calls = []
        self.screenshot_calls = []
        self.viewports = []

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    async def evaluate(self, script, *args):
        return None

    async def wait_for_selector(self, selector, **kwargs):
        return None

    async def wait_for_timeout(self, timeout):
        return None

    async def screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)

    async def set_viewport_size(self, viewport):
        self.viewports.append(viewport)

    async def add_style_tag(self, **kwargs):
        return None

    async def query_selector(self, selector):
        return None


class _FakeContext:
    def __init__(self, page):
        self.page = page
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.closed = False

    async def new_context(self, **kwargs):
        return self.context

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    async def launch(self, **kwargs):
        assert kwargs == {"headless": True}
        return self.browser


class _FakeRuntime:
    def __init__(self, chromium):
        self.chromium = chromium


class _FakeManager:
    def __init__(self, runtime):
        self.runtime = runtime

    async def __aenter__(self):
        return self.runtime

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_capture_screenshots_owns_browser_and_paths(monkeypatch, tmp_path):
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    manager = _FakeManager(_FakeRuntime(_FakeChromium(browser)))

    monkeypatch.setattr(
        leveltravel_screenshots,
        "async_playwright",
        lambda: manager,
    )
    monkeypatch.setattr(
        leveltravel_screenshots.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )

    paths = asyncio.run(
        leveltravel_screenshots.capture_hotel_screenshots(
            "https://level.travel/hotels/test",
            "Hotel / Test!?",
            7,
            leveltravel.SEARCH_TYPE_TOUR,
        )
    )

    expected_calendar = (
        tmp_path / "tour_screenshots" / "Hotel  Test_1_calendar.png"
    )
    assert paths == [str(expected_calendar)]
    assert page.goto_calls == [
        (
            "https://level.travel/hotels/test",
            {"timeout": 60000, "wait_until": "domcontentloaded"},
        )
    ]
    assert page.screenshot_calls[0]["path"] == str(expected_calendar)
    assert page.viewports == [
        {"width": 1920, "height": 2000},
        {"width": 1920, "height": 1080},
    ]
    assert context.closed is True
    assert browser.closed is True


def test_leveltravel_reexports_screenshot_api():
    assert (
        leveltravel.capture_hotel_screenshots
        is leveltravel_screenshots.capture_hotel_screenshots
    )
