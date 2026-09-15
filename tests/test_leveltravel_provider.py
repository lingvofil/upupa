import asyncio

from AI import leveltravel, leveltravel_provider


class _FakeMouse:
    def __init__(self):
        self.wheels = []

    async def wheel(self, x, y):
        self.wheels.append((x, y))


class _FakePage:
    def __init__(self, evaluate_result):
        self.evaluate_result = evaluate_result
        self.goto_calls = []
        self.wait_selectors = []
        self.wait_timeouts = []
        self.mouse = _FakeMouse()

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))

    async def wait_for_selector(self, selector, **kwargs):
        self.wait_selectors.append((selector, kwargs))

    async def wait_for_timeout(self, timeout):
        self.wait_timeouts.append(timeout)

    async def evaluate(self, script):
        return self.evaluate_result


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
        self.context_kwargs = None
        self.closed = False

    async def new_context(self, **kwargs):
        self.context_kwargs = kwargs
        return self.context

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_kwargs = None

    async def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class _FakePlaywrightRuntime:
    def __init__(self, chromium):
        self.chromium = chromium


class _FakePlaywrightManager:
    def __init__(self, runtime):
        self.runtime = runtime

    async def __aenter__(self):
        return self.runtime

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _install_fake_playwright(monkeypatch, evaluate_result):
    page = _FakePage(evaluate_result)
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FakeChromium(browser)
    runtime = _FakePlaywrightRuntime(chromium)
    manager = _FakePlaywrightManager(runtime)
    monkeypatch.setattr(leveltravel_provider, "async_playwright", lambda: manager)
    return page, context, browser, chromium


def test_quick_price_scan_uses_search_plan_and_closes_browser(monkeypatch):
    page, context, browser, chromium = _install_fake_playwright(monkeypatch, 123456)

    price = asyncio.run(
        leveltravel_provider.quick_price_scan(
            "VN",
            "18.05.2026",
            2,
            7,
            leveltravel.SEARCH_TYPE_TOUR,
            destination_slug="Phu.Quoc-VN",
        )
    )

    assert price == 123456
    assert page.goto_calls[0][0] == (
        "https://level.travel/search/"
        "Moscow-RU-to-Phu.Quoc-VN-departure-18.05.2026-"
        "for-6..8-nights-2-adults-0-kids-1..5-stars-package-type"
    )
    assert chromium.launch_kwargs == {"headless": True}
    assert context.closed is True
    assert browser.closed is True


def test_deep_parse_date_returns_provider_rows_and_scrolls(monkeypatch):
    rows = [
        {
            "hotel_name": "Test Hotel",
            "price": 150000,
            "rating": 8.8,
            "stars": 4,
            "location": "Phu Quoc",
            "link": "https://level.travel/hotels/test",
            "nights": 7,
        }
    ]
    page, context, browser, _ = _install_fake_playwright(monkeypatch, rows)

    result = asyncio.run(
        leveltravel_provider.deep_parse_date(
            "VN",
            "18.05.2026",
            2,
            7,
            leveltravel.SEARCH_TYPE_HOTEL,
            destination_slug="Phu.Quoc-VN",
        )
    )

    assert result == rows
    assert page.goto_calls[0][0].startswith(
        "https://level.travel/search/Any-RU-to-Phu.Quoc-VN-"
    )
    assert page.mouse.wheels == [(0, 1500)] * 10
    assert page.wait_timeouts == [1500] * 10
    assert context.closed is True
    assert browser.closed is True


def test_leveltravel_reexports_provider_api():
    assert leveltravel.quick_price_scan is leveltravel_provider.quick_price_scan
    assert leveltravel.deep_parse_date is leveltravel_provider.deep_parse_date
