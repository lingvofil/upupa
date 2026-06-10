import aiohttp
import json
from transliterate import translit

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
        
        # Получаем информацию о имени
        info = await get_name_info(name)
        
        # Формируем ответ
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
