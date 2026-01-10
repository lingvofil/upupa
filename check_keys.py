import google.generativeai as genai
import os

# Попытка импортировать ключи так же, как в вашем боте
try:
    from config_private import (
        GENERIC_API_KEY, GENERIC_API_KEY2, GENERIC_API_KEY3, 
        GENERIC_API_KEY4, GENERIC_API_KEY5, GENERIC_API_KEY6, 
        GOOGLE_API_KEY, GOOGLE_API_KEY2
    )
    print("✅ Ключи успешно загружены из config_private")
except ImportError:
    print("⚠️ config_private не найден, проверьте, где лежат ключи.")
    # Если файла нет, можно временно вписать ключи вручную ниже:
    GENERIC_API_KEY = "ВСТАВЬТЕ_КЛЮЧ_ЕСЛИ_НУЖНО"
    # ... остальные = None

# Словарь для проверки: Имя переменной -> Сам ключ
keys_to_test = {
    "GENERIC_API_KEY": GENERIC_API_KEY,
    "GENERIC_API_KEY2": GENERIC_API_KEY2,
    "GENERIC_API_KEY3": GENERIC_API_KEY3,
    "GENERIC_API_KEY4": GENERIC_API_KEY4,
    "GENERIC_API_KEY5": GENERIC_API_KEY5,
    "GENERIC_API_KEY6": GENERIC_API_KEY6,
    "GOOGLE_API_KEY": GOOGLE_API_KEY,
    "GOOGLE_API_KEY2": GOOGLE_API_KEY2
}

# Модель для проверки (берем самую легкую)
TEST_MODEL = 'gemini-2.0-flash'

print(f"\n--- НАЧИНАЮ ПРОВЕРКУ КЛЮЧЕЙ НА МОДЕЛИ {TEST_MODEL} ---\n")

for key_name, api_key in keys_to_test.items():
    if not api_key:
        print(f"⚪ {key_name}: Пропущен (пустой или None)")
        continue

    # Маскируем ключ для вывода
    masked_key = f"...{api_key[-4:]}"
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(TEST_MODEL)
    
    try:
        # Пробуем сделать простейший запрос
        response = model.generate_content("Hi")
        print(f"✅ {key_name} ({masked_key}): РАБОТАЕТ! (Ответ получен)")
        
    except Exception as e:
        error_msg = str(e)
        
        if "User location is not supported" in error_msg:
            print(f"❌ {key_name} ({masked_key}): ОШИБКА 400 (ГЕОЛОКАЦИЯ)")
            print("   -> Этот ключ блокируется Google из-за вашего IP. Нужен VPN/Прокси.")
            
        elif "Generative Language API has not been used" in error_msg or "SERVICE_DISABLED" in error_msg:
            print(f"🚫 {key_name} ({masked_key}): ОШИБКА 403 (API ОТКЛЮЧЕН)")
            # Пытаемся вытащить ссылку активации из текста ошибки
            import re
            url_match = re.search(r'https://console\.developers\.google\.com/apis/api/generativelanguage\.googleapis\.com/overview\?project=\d+', error_msg)
            if url_match:
                print(f"   -> ВКЛЮЧИТЕ ЗДЕСЬ: {url_match.group(0)}")
            else:
                print("   -> Зайдите в Google Cloud Console и включите 'Generative Language API'.")
                
        elif "Quota exceeded" in error_msg:
            print(f"⏳ {key_name} ({masked_key}): ОШИБКА 429 (ЛИМИТЫ ИСЧЕРПАНЫ)")
            print("   -> Ключ рабочий, но на сегодня квота кончилась.")
            
        else:
            print(f"⚠️ {key_name} ({masked_key}): НЕИЗВЕСТНАЯ ОШИБКА")
            print(f"   Текст ошибки: {error_msg[:200]}...") # Печатаем начало ошибки
            
    print("-" * 40)
