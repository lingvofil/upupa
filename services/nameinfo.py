import asyncio
import logging

import aiohttp
from transliterate import translit

from core.state import chat_settings
from infrastructure.ai.clients import gigachat_model, groq_ai, model


_NAME_PROFILE_PROMPT = """Ты составляешь краткую справку о личном имени на русском языке.

Имя: {name}

Верни только готовую справку, без вступлений и предложений продолжить разговор.
Структура обязательна:
Имя - {name}. Кратко объясни наиболее принятую этимологию и значение имени в 1-2 предложениях.

📜 Происхождение и история

· 1-2 коротких факта о происхождении и истории.

🌍 Распространение в мире

1 короткий абзац о странах/культурах, где имя встречается.

💡 Интересные факты

· 2-3 коротких факта.

Правила:
- не добавляй возраст, пол и национальность: они будут добавлены отдельно;
- не используй Markdown-разметку;
- не выдумывай спорные конкретные даты, святых, исторических персонажей или родственные имена;
- если происхождение или факт неоднозначны, прямо напиши, что существуют разные версии;
- общий объём — примерно 700-1200 знаков.
"""


async def transliterate_to_english(name: str) -> str:
    """
    Транслитерирует имя с кириллицы на латиницу
    """
    try:
        # Попытка транслитерации
        transliterated = translit(name, 'ru', reversed=True)
        return transliterated
    except Exception as e:
        print(f"Ошибка при транслитерации: {e}")
        # Простая замена кириллических символов (запасной вариант)
        replacements = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
        }
        result = ''
        for char in name.lower():
            result += replacements.get(char, char)
        return result.capitalize()


async def get_name_info(name: str) -> dict:
    """
    Получает информацию о имени из трех API: возраст, пол и национальность
    """
    results = {
        "name": name,
        "age": None,
        "gender": None,
        "nationality": None
    }

    # Сначала пробуем с оригинальным именем
    await fetch_api_data(name, results)

    # Если какие-то данные отсутствуют, пробуем транслитерированное имя
    if results["age"] is None or results["gender"] is None or results["nationality"] is None:
        eng_name = await transliterate_to_english(name)
        if eng_name != name:  # Убедимся, что транслитерация изменила имя
            temp_results = {
                "name": name,  # сохраняем оригинальное имя
                "age": None,
                "gender": None,
                "nationality": None
            }
            await fetch_api_data(eng_name, temp_results)

            # Заменяем только отсутствующие данные
            if results["age"] is None and temp_results["age"] is not None:
                results["age"] = temp_results["age"]

            if results["gender"] is None and temp_results["gender"] is not None:
                results["gender"] = temp_results["gender"]
                results["gender_probability"] = temp_results.get("gender_probability")

            if results["nationality"] is None and temp_results["nationality"] is not None:
                results["nationality"] = temp_results["nationality"]

    return results


async def fetch_api_data(name: str, results: dict):
    """
    Вспомогательная функция для получения данных из API
    """
    # Формируем URLs для API запросов
    age_url = f"https://api.agify.io/?name={name}"
    gender_url = f"https://api.genderize.io/?name={name}"
    nationality_url = f"https://api.nationalize.io/?name={name}"

    # Асинхронно отправляем запросы ко всем API
    async with aiohttp.ClientSession() as session:
        # Запрос возраста
        try:
            async with session.get(age_url) as response:
                if response.status == 200:
                    data = await response.json()
                    results["age"] = data.get("age")
        except Exception as e:
            print(f"Ошибка при получении возраста: {e}")

        # Запрос пола
        try:
            async with session.get(gender_url) as response:
                if response.status == 200:
                    data = await response.json()
                    gender = data.get("gender")
                    if gender == "male":
                        results["gender"] = "мужской"
                    elif gender == "female":
                        results["gender"] = "женский"
                    probability = data.get("probability", 0)
                    results["gender_probability"] = probability
        except Exception as e:
            print(f"Ошибка при получении пола: {e}")

        # Запрос национальности
        try:
            async with session.get(nationality_url) as response:
                if response.status == 200:
                    data = await response.json()
                    countries = data.get("country", [])
                    if countries:
                        # Сортируем по вероятности и берем топ-2 страны
                        countries.sort(key=lambda x: x.get("probability", 0), reverse=True)
                        top_countries = countries[:2]
                        countries_info = []
                        for country in top_countries:
                            country_id = country.get("country_id")
                            probability = country.get("probability", 0)
                            countries_info.append(f"{country_id} ({int(probability * 100)}%)")
                        results["nationality"] = ", ".join(countries_info)
        except Exception as e:
            print(f"Ошибка при получении национальности: {e}")


async def generate_name_profile(name: str, chat_id: int) -> str | None:
    """Генерирует расширенную справку об имени через активную модель чата."""
    prompt = _NAME_PROFILE_PROMPT.format(name=name)
    active_model = chat_settings.get(str(chat_id), {}).get("active_model", "gemini")
    if active_model == "history":
        active_model = "gemini"

    def sync_generate() -> str:
        if active_model == "gigachat":
            response = gigachat_model.generate_content(prompt, chat_id=chat_id)
            return response.text.strip()
        if active_model == "groq":
            return groq_ai.generate_text(prompt).strip()

        response = model.generate_content(prompt, chat_id=chat_id)
        return response.text.strip()

    try:
        profile = await asyncio.to_thread(sync_generate)
        return profile or None
    except Exception:
        logging.exception("Не удалось сгенерировать расширенную справку об имени %r", name)
        return None


async def process_name_info(message):
    """
    Обрабатывает команду 'имя <имя>' и возвращает информацию
    """
    try:
        text = message.text.strip()

        # Проверяем без учета регистра
        if not text.lower().startswith("имя "):
            return False, "Хуимя"

        # Извлекаем имя после первых 4 символов ('имя ')
        name = text[4:].strip()

        if not name:
            return False, "Долбоеб, напиши, например: 'имя Женя'"

        # Независимые источники запускаем параллельно, чтобы расширенная справка
        # не добавляла к команде лишнюю последовательную задержку.
        info_task = asyncio.create_task(get_name_info(name))
        profile_task = asyncio.create_task(generate_name_profile(name, message.chat.id))
        info, profile = await asyncio.gather(info_task, profile_task)

        # Если AI-справка недоступна, сохраняем прежний короткий формат.
        if profile:
            response = f"{profile}\n\n"
        else:
            response = f"Имя - {info['name']}\n"

        response += f"Возраст - {info['age'] if info['age'] is not None else 'Не определен'}\n"

        gender_text = info.get('gender', 'Не определен')
        if gender_text != 'Не определен' and info.get('gender_probability'):
            gender_text += f" (вероятность: {int(info['gender_probability'] * 100)}%)"
        response += f"Пол - {gender_text}\n"

        response += f"Национальность - {info.get('nationality', 'Не определена')}"

        return True, response
    except Exception as e:
        print(f"Ошибка при обработке имени: {e}")
        return False, "Произошла ошибка при обработке запроса. Пожалуйста, пройдите нахуй."
