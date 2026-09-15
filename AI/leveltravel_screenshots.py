import logging
import os
import re
from typing import List

from playwright.async_api import async_playwright

from AI.leveltravel_parsing import SEARCH_TYPE_TOUR


async def capture_hotel_screenshots(
    hotel_link: str,
    hotel_name: str,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
) -> List[str]:
    """Создает ДВА скриншота: календарь и варианты номеров."""
    paths = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
            )
            page = await context.new_page()

            try:
                logging.info(
                    f"Создаю скриншоты для {hotel_name} (тип: {search_type})"
                )
                await page.goto(
                    hotel_link, timeout=60000, wait_until="domcontentloaded"
                )

                await page.evaluate(
                    """
                    () => {
                        const selectors = [
                            '[class*="CookieConsent"]',
                            '[class*="WidgetContainer"]',
                            '#jivo-iframe-container',
                            '[class*="StickyButton"]',
                            '[class*="HeaderWrapper"]',
                            '[class*="StickyFilter"]',
                            '[class*="StickyPrice"]',
                            '[class*="Floating"]'
                        ];
                        selectors.forEach(s => {
                            const el = document.querySelector(s);
                            if (el) el.style.display = 'none';
                        });
                    }
                    """
                )

                try:
                    await page.wait_for_selector(
                        '[class*="Calendar"], [class*="PriceGrid"], '
                        '[class*="HotelHeader"], .hotel-content',
                        timeout=20000,
                    )
                except Exception:
                    logging.warning(
                        f"Контент для {hotel_name} не найден по селекторам"
                    )

                await page.wait_for_timeout(2000)

                await page.evaluate(
                    """
                    () => {
                        const target = document.querySelector('[class*="Calendar"]') ||
                                       document.querySelector('[class*="PriceGrid"]') ||
                                       document.querySelector('[class*="HotelHeader"]');
                        if (target) {
                            target.scrollIntoView({ behavior: 'auto', block: 'center' });
                        }
                    }
                    """
                )
                await page.wait_for_timeout(1000)

                screenshots_dir = "/tmp/tour_screenshots"
                os.makedirs(screenshots_dir, exist_ok=True)
                safe_name = re.sub(r"[^\w\s-]", "", hotel_name)[:50]

                path1 = f"{screenshots_dir}/{safe_name}_1_calendar.png"
                await page.screenshot(path=path1, full_page=False, type="png")
                paths.append(path1)
                path2 = f"{screenshots_dir}/{safe_name}_2_rooms.png"

                await page.set_viewport_size({"width": 1920, "height": 2000})

                try:
                    await page.wait_for_selector(
                        '[class*="Skeleton"], [class*="Loader"], '
                        '[class*="Placeholder"]',
                        state="detached",
                        timeout=5000,
                    )
                except Exception:
                    pass

                target_selector = 'article[class*="HotelRoomCard_roomCard"]'
                try:
                    await page.wait_for_selector(
                        target_selector, state="attached", timeout=20000
                    )
                except Exception:
                    target_selector = (
                        'div[class*="HotelRoom"]:not([class*="Container"])'
                    )

                await page.add_style_tag(
                    content="""
                    header, [class*="Header"], [class*="Sticky"], [class*="Filter"], [class*="Head"], #header {
                        display: none !important;
                        opacity: 0 !important;
                        pointer-events: none !important;
                    }
                    """
                )

                await page.wait_for_timeout(500)
                element = await page.query_selector(target_selector)

                if element:
                    await page.evaluate(
                        "el => el.scrollIntoView({block: 'center'})", element
                    )
                    await page.wait_for_timeout(1500)
                    box = await element.bounding_box()

                    if box:
                        await page.screenshot(
                            path=path2,
                            full_page=False,
                            clip={
                                "x": 0,
                                "y": box["y"],
                                "width": 1920,
                                "height": 1500,
                            },
                        )
                        paths.append(path2)
                    else:
                        logging.warning(
                            "Не удалось получить координаты bounding_box"
                        )
                        await page.screenshot(path=path2, full_page=False)
                        paths.append(path2)
                else:
                    logging.warning(
                        f"Элемент {target_selector} не найден после ожидания"
                    )

                await page.set_viewport_size({"width": 1920, "height": 1080})

                logging.info(f"Скриншоты созданы: {len(paths)}")
                return paths

            finally:
                await context.close()
                await browser.close()

    except Exception as e:
        logging.error(f"Ошибка захвата экрана для {hotel_name}: {e}")
        return paths
