import logging
import random
from datetime import datetime
from typing import List, Tuple

import aiohttp
from aiogram import types

from core.loader import bot
from core.settings import OPENWEATHER_API_KEY
from prompts import actions


logger = logging.getLogger(__name__)
OPENWEATHER_TIMEOUT_SECONDS = 10

CITIES = [
    "Moscow", "Odintsovo", "Saint Petersburg", "Arkhangelsk", "Vladikavkaz", "Kazan",
    "Omsk", "Irkutsk", "Slyudyanka", "Baykalsk", "Kyakhta",
    "Angarsk", "Minsk", "Sochi", "Samara", "Almaty",
]
CITY_NAMES = {
    "Moscow": "Москва",
    "Odintsovo": "Одинцово",
    "Saint Petersburg": "Санкт-Петербург",
    "Arkhangelsk": "Архангельск",
    "Vladikavkaz": "Владикавказ",
    "Kazan": "Казань",
    "Omsk": "Омск",
    "Irkutsk": "Иркутск",
    "Slyudyanka": "Слюдянка",
    "Baykalsk": "Байкальск",
    "Kyakhta": "Кяхта",
    "Angarsk": "Ангарск",
    "Minsk": "Минск",
    "Sochi": "Сочи",
    "Samara": "Самара",
    "Almaty": "Алматы",
}

WeatherReading = Tuple[int | None, str]
CityWeather = Tuple[str, int | None, str]


async def handle_current_weather_command(message: types.Message):
    """Полностью обрабатывает команду «упупа погода»."""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Высовываю руку в акно...")

    try:
        weather_report = await get_weather_with_fallback()
        await processing_msg.delete()
        await message.reply(weather_report)
    except Exception as exc:
        logging.error(
            "Критическая ошибка в handle_current_weather_command: %s",
            exc,
            exc_info=True,
        )
        try:
            await processing_msg.delete()
        except Exception:
            pass
        await message.reply("Не удалось получить погоду. OpenWeather сейчас недоступен.")


async def handle_weekly_forecast_command(message: types.Message):
    """Полностью обрабатывает команду «погода неделя»."""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))

    try:
        city = message.text.lower().replace("погода неделя", "").strip()
        if not city:
            await message.reply("Укажите город, уважаемое чучело, после команды 'погода неделя'")
            return

        processing_msg = await message.reply("Плюю на палец, зодумчиво поднимаю вверх...")
        forecast = await format_weekly_forecast(city)
        await processing_msg.delete()
        await message.reply(forecast)
    except Exception as exc:
        logging.error("Ошибка в handle_weekly_forecast_command: %s", exc, exc_info=True)
        await message.reply("Произошла ошибка при получении прогноза.")


async def get_weather(city: str) -> WeatherReading:
    """Получить текущую погоду; ошибка представляется отсутствием измерения, а не нулём."""
    if not OPENWEATHER_API_KEY:
        return None, "ключ OpenWeather не настроен"

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
        "lang": "ru",
    }
    timeout = aiohttp.ClientTimeout(total=OPENWEATHER_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return None, f"ошибка API: {response.status}"
                data = await response.json()
                temperature = round(float(data["main"]["temp"]))
                description = str(data["weather"][0]["description"])
                return temperature, description
    except Exception as exc:
        logging.warning("OpenWeather current failed city=%s error=%s", city, exc)
        return None, f"ошибка: {type(exc).__name__}"


async def get_all_cities_weather() -> List[CityWeather]:
    result = []
    for city_en in CITIES:
        temp, desc = await get_weather(city_en)
        result.append((CITY_NAMES.get(city_en, city_en), temp, desc))
    return result


async def format_weather_report() -> str:
    """Форматирует только реальные измерения и явно отмечает недоступные города."""
    cities_weather = await get_all_cities_weather()
    available = [item for item in cities_weather if item[1] is not None]
    unavailable = [item for item in cities_weather if item[1] is None]

    if not available:
        return "Не удалось получить погоду: OpenWeather не вернул данные ни по одному городу."

    available.sort(key=lambda item: int(item[1]), reverse=True)
    weather_lines = []
    for index, (city, temp, _description) in enumerate(available, 1):
        sign = "+" if int(temp) > 0 else ""
        weather_lines.append(f"{index}. {city} {sign}{temp}")

    if unavailable:
        weather_lines.append("")
        weather_lines.append(
            "Нет данных: " + ", ".join(city for city, _temp, _desc in unavailable)
        )

    lowest_temp_city = available[-1][0]
    weather_lines.append("")
    weather_lines.append(f"{lowest_temp_city} - лошки")
    return "\n".join(weather_lines)


async def get_weekly_forecast(city: str) -> List[Tuple[str, int, str]]:
    if not OPENWEATHER_API_KEY:
        return []

    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "q": city,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
        "lang": "ru",
    }
    timeout = aiohttp.ClientTimeout(total=OPENWEATHER_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return []
                data = await response.json()
                daily_forecasts = {}
                current_date = datetime.now().date()
                for item in data["list"]:
                    date = datetime.fromtimestamp(item["dt"]).date()
                    if date >= current_date and len(daily_forecasts) < 7:
                        if date not in daily_forecasts:
                            daily_forecasts[date] = {
                                "temps": [],
                                "description": item["weather"][0]["description"],
                            }
                        daily_forecasts[date]["temps"].append(float(item["main"]["temp"]))

                result = []
                for date, forecast in daily_forecasts.items():
                    avg_temp = round(sum(forecast["temps"]) / len(forecast["temps"]))
                    result.append((date.strftime("%d.%m"), avg_temp, forecast["description"]))
                return result
    except Exception as exc:
        logging.warning("OpenWeather forecast failed city=%s error=%s", city, exc)
        return []


async def format_weekly_forecast(city: str) -> str:
    forecast = await get_weekly_forecast(city)
    if not forecast:
        return f"Не удалось получить прогноз погоды для города {city}"

    result = [f"В ссаном гораде {city}:"]
    for date, temp, condition in forecast:
        sign = "+" if temp > 0 else ""
        result.append(f"{date}: {sign}{temp}°, {condition}")
    return "\n".join(result)


async def get_weather_with_fallback() -> str:
    """Совместимое имя без подстановки выдуманных тестовых температур."""
    return await format_weather_report()
