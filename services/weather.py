import aiohttp
import json
import logging
import random
from typing import Dict, Tuple, List
from datetime import datetime, timedelta
from aiogram import types

# Убедитесь, что все зависимости импортированы
from config import bot
from prompts import actions

# Настройка логирования для отладки
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API-ключ OpenWeatherMap
OPENWEATHER_API_KEY = "7cf485d99b222b17e90f6d57df6c2d7d"

# Список городов
CITIES = [
    "Moscow", "Odintsovo", "Saint Petersburg", "Vladikavkaz", "Kazan", 
    "Omsk", "Irkutsk", "Slyudyanka", "Baykalsk", "Kyakhta",
    "Angarsk", "Minsk", "Sochi", "Samara", "Almaty"
]

# =============================================================================
# НОВЫЕ ФУНКЦИИ-ОБРАБОТЧИКИ
# =============================================================================

async def handle_current_weather_command(message: types.Message):
    """
    Полностью обрабатывает команду 'упупа погода'.
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Высовываю руку в акно...")
    
    try:
        weather_report = await get_weather_with_fallback()
        await processing_msg.delete()
        await message.reply(weather_report)
    except Exception as e:
        logging.error(f"Критическая ошибка в handle_current_weather_command: {e}", exc_info=True)
        try:
            await processing_msg.delete()
        except Exception:
            pass # Сообщение могло быть уже удалено
        mock_data = await get_mock_weather()
        await message.reply(f"{mock_data}\n\n(Использованы тестовые данные из-за ошибки: {str(e)})")

async def handle_weekly_forecast_command(message: types.Message):
    """
    Полностью обрабатывает команду 'погода неделя'.
    """
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
        
    except Exception as e:
        logging.error(f"Ошибка в handle_weekly_forecast_command: {e}", exc_info=True)
        await message.reply(f"Произошла ошибка при получении прогноза: {str(e)}")


# (Остальные функции остаются без изменений ниже)

async def get_weather(city: str) -> Tuple[int, str]:
    # ... (код функции без изменений)
    url = "http://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "ru"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    temperature = round(float(data["main"]["temp"]))
                    description = data["weather"][0]["description"]
                    return (temperature, description)
                else:
                    return (0, f"ошибка API: {response.status}")
    except Exception as e:
        return (0, f"ошибка: {str(e)}")

async def get_all_cities_weather() -> List[Tuple[str, int, str]]:
    # ... (код функции без изменений)
    result = []
    city_map = {
        "Moscow": "Москва", "Odintsovo": "Одинцово", "Saint Petersburg": "Санкт-Петербург",
        "Vladikavkaz": "Владикавказ", "Kazan": "Казань", "Omsk": "Омск",
        "Irkutsk": "Иркутск", "Slyudyanka": "Слюдянка", "Baykalsk": "Байкальск",
        "Kyakhta": "Кяхта", "Angarsk": "Ангарск", "Minsk": "Минск",
        "Sochi": "Сочи", "Samara": "Самара", "Almaty": "Алматы"
    }
    for city_en in CITIES:
        temp, desc = await get_weather(city_en)
        city_ru = city_map.get(city_en, city_en)
        result.append((city_ru, temp, desc))
    return result

async def format_weather_report() -> str:
    # ... (код функции без изменений)
    cities_weather = await get_all_cities_weather()
    cities_weather.sort(key=lambda x: x[1], reverse=True)
    weather_lines = []
    lowest_temp_city = cities_weather[-1][0]
    for i, (city, temp, _) in enumerate(cities_weather, 1):
        sign = "+" if temp > 0 else ""
        if temp == 0: sign = ""
        weather_lines.append(f"{i}. {city} {sign}{temp}")
    report = "\n".join(weather_lines)
    report += f"\n\n{lowest_temp_city} - лошки"
    return report

async def get_weekly_forecast(city: str) -> List[Tuple[str, int, str]]:
    # ... (код функции без изменений)
    url = "http://api.openweathermap.org/data/2.5/forecast"
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "ru"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    daily_forecasts = {}
                    current_date = datetime.now().date()
                    for item in data['list']:
                        date = datetime.fromtimestamp(item['dt']).date()
                        if date >= current_date and len(daily_forecasts) < 7:
                            if date not in daily_forecasts:
                                daily_forecasts[date] = {'temps': [], 'description': item['weather'][0]['description']}
                            daily_forecasts[date]['temps'].append(float(item['main']['temp']))
                    result = []
                    for date, forecast in daily_forecasts.items():
                        avg_temp = round(sum(forecast['temps']) / len(forecast['temps']))
                        result.append((date.strftime("%d.%m"), avg_temp, forecast['description']))
                    return result
                else:
                    return []
    except Exception:
        return []

async def format_weekly_forecast(city: str) -> str:
    # ... (код функции без изменений)
    forecast = await get_weekly_forecast(city)
    if not forecast:
        return f"Не удалось получить прогноз погоды для города {city}"
    result = [f"В ссаном гораде {city}:"]
    for date, temp, condition in forecast:
        sign = "+" if temp > 0 else ""
        if temp == 0: sign = ""
        result.append(f"{date}: {sign}{temp}°, {condition}")
    return "\n".join(result)

async def get_mock_weather() -> str:
    # ... (код функции без изменений)
    mock_data = [
        ("Омск", 33), ("Сочи", 15), ("Владикавказ", 12),
        ("Самара", 5), ("Москва", 4), ("Казань", 3), ("Минск", 2),
        ("Кяхта", 0), ("Одинцово", -2), ("Слюдянка", -3), ("Ангарск", -5),
        ("Байкальск", -22), ("Иркутск", -23)
    ]
    lines = []
    for i, (city, temp) in enumerate(mock_data, 1):
        sign = "+" if temp > 0 else ""
        if temp == 0: sign = ""
        lines.append(f"{i}. {city} {sign}{temp}")
    lines.append("\nИркутск - лошки")
    return "\n".join(lines)

async def get_weather_with_fallback() -> str:
    # ... (код функции без изменений)
    try:
        weather_report = await format_weather_report()
        if "ошибка API" in weather_report or "ошибка:" in weather_report:
            return await get_mock_weather()
        return weather_report
    except Exception as e:
        return await get_mock_weather() + f"\n\n(Ошибка API: {str(e)})"

