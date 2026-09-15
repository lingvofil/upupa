"""Playwright-backed LevelTravel search data provider."""

import logging
from typing import Dict, List, Optional

from playwright.async_api import async_playwright

from AI.leveltravel_parsing import SEARCH_TYPE_HOTEL, SEARCH_TYPE_TOUR
from AI.leveltravel_search_plan import build_search_url


async def quick_price_scan(
    country_code: str,
    date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
    destination_slug: Optional[str] = None,
) -> Optional[int]:
    """Quickly scan the first LevelTravel result and return its price."""
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
                search_url = build_search_url(
                    country_code,
                    date,
                    adults,
                    nights,
                    search_type,
                    destination_slug=destination_slug,
                )

                await page.goto(
                    search_url,
                    timeout=60000,
                    wait_until="domcontentloaded",
                )

                try:
                    await page.wait_for_selector(
                        'div[class*="DesktopHotelCard_container"]',
                        timeout=15000,
                    )
                except Exception:
                    logging.warning(f"Нет результатов для {date}")
                    return None

                await page.wait_for_timeout(1000)

                if search_type == SEARCH_TYPE_HOTEL:
                    price_selector = (
                        'div[class*="HotelCardPriceBlock_styledHotelCardPrice"]'
                    )
                else:
                    price_selector = 'div[class*="HotelCardPriceBlock_styledPrice"]'

                min_price = await page.evaluate(
                    f"""
                    () => {{
                        const firstCard = document.querySelector('div[class*="DesktopHotelCard_container"]');
                        if (!firstCard) return null;

                        const priceEl = firstCard.querySelector('{price_selector}');
                        if (!priceEl) return null;

                        const priceText = priceEl.textContent.replace(/\\s/g, '').replace(/&nbsp;/g, '').replace(/\\u00a0/g, '');
                        const priceMatch = priceText.match(/(\\d+)/);
                        return priceMatch ? parseInt(priceMatch[0]) : null;
                    }}
                    """
                )

                if min_price:
                    logging.info(f"Найдена цена для {date}: {min_price} ₽")

                return min_price

            finally:
                await context.close()
                await browser.close()

    except Exception as exc:
        logging.error(f"Ошибка quick_price_scan для {date}: {exc}")
        return None


async def deep_parse_date(
    country_code: str,
    date: str,
    adults: int,
    nights: int,
    search_type: str = SEARCH_TYPE_TOUR,
    destination_slug: Optional[str] = None,
) -> List[Dict]:
    """Deep-parse LevelTravel hotel cards for a single search date."""
    tours = []

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
                search_url = build_search_url(
                    country_code,
                    date,
                    adults,
                    nights,
                    search_type,
                    destination_slug=destination_slug,
                )

                logging.info(
                    f"Глубокий парсинг: {date} "
                    f"({nights} ночей, тип: {search_type})"
                )
                await page.goto(
                    search_url,
                    timeout=90_000,
                    wait_until="domcontentloaded",
                )

                try:
                    await page.wait_for_selector(
                        'div[class*="DesktopHotelCard_container"]',
                        timeout=40_000,
                    )
                except Exception:
                    logging.warning(f"Карточки не загрузились для {date}")
                    return []

                for _ in range(10):
                    await page.mouse.wheel(0, 1500)
                    await page.wait_for_timeout(1500)

                price_selector = (
                    'div[class*="HotelCardPriceBlock_styledHotelCardPrice"]'
                    if search_type == SEARCH_TYPE_HOTEL
                    else 'div[class*="HotelCardPriceBlock_styledPrice"]'
                )

                tours = await page.evaluate(
                    f"""
                    () => {{
                        const results = [];
                        const cards = Array.from(
                            document.querySelectorAll(
                                'div[class*="DesktopHotelCard_container"]'
                            )
                        );

                        for (const card of cards) {{
                            try {{
                                const tour = {{
                                    hotel_name: "Без названия",
                                    price: 0,
                                    rating: 0,
                                    stars: 0,
                                    location: "",
                                    link: "",
                                    nights: 0
                                }};

                                const titleEl = card.querySelector(
                                    'a[class*="HotelCardTitle_title"]'
                                );
                                if (titleEl) {{
                                    tour.hotel_name = titleEl.textContent.trim();
                                    tour.link = titleEl.getAttribute("href");
                                    if (tour.link && !tour.link.startsWith("http")) {{
                                        tour.link = "https://level.travel" + tour.link;
                                    }}
                                }}

                                const priceEl = card.querySelector(
                                    '{price_selector}'
                                );
                                if (priceEl) {{
                                    const text = priceEl.textContent
                                        .replace(/\\s/g, "")
                                        .replace(/\\u00a0/g, "");
                                    const m = text.match(/(\\d+)/);
                                    if (m) tour.price = parseInt(m[1], 10);
                                }}

                                const locEl = card.querySelector(
                                    'p[class*="HotelCardLocation_text"]'
                                );
                                if (locEl) tour.location = locEl.textContent.trim();

                                const ratingEl = card.querySelector(
                                    'span[class*="HotelRating_rating"]'
                                );
                                if (ratingEl) {{
                                    tour.rating = parseFloat(
                                        ratingEl.textContent.trim()
                                    );
                                }}

                                const starsEl = card.querySelector(
                                    'div[class*="HotelStars_container"]'
                                );
                                if (starsEl) {{
                                    tour.stars = starsEl.querySelectorAll("svg").length;
                                }}

                                if (tour.link) {{
                                    const nightsMatch = tour.link.match(/for-(\\d+)-nights/);
                                    if (nightsMatch) {{
                                        tour.nights = parseInt(nightsMatch[1], 10);
                                    }}
                                }}

                                if (tour.price > 1000 && tour.hotel_name !== "Без названия") {{
                                    results.push(tour);
                                }}
                            }} catch (e) {{}}
                        }}

                        return results;
                    }}
                    """
                )

            finally:
                await context.close()
                await browser.close()

    except Exception as exc:
        logging.error(f"Ошибка deep_parse_date для {date}: {exc}")

    return tours
