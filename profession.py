# profession.py
import requests
import io
import codecs
import re
import random
from Config import model # Импортируем модель Gemini из Config.py
from Prompts import actions # <--- ИЗМЕНЕНО: Импортируем actions из Prompts.py

# URL для загрузки файла ОКВЭД
OKVED_URL = "https://classifikators.ru/assets/downloads/okved/okved.csv"

async def get_random_okved_and_commentary(message):
    """
    Скачивает случайное описание ОКВЭД, получает саркастичный комментарий от ИИ,
    и отправляет его в чат.
    """
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Ищу, кем бы тебе стать, обоссанец...")

    try:
        # 1. Скачиваем файл ОКВЭД
        response = requests.get(OKVED_URL)
        response.raise_for_status() # Проверка на ошибки HTTP

        # 2. Декодируем содержимое из windows-1251 в utf-8
        decoded_content = codecs.decode(response.content, 'windows-1251')
        file_like_object = io.StringIO(decoded_content)

        descriptions = []
        for line in file_like_object:
            # 3. Извлекаем описание после первого ';'
            match = re.search(r';\s*(.*)', line)
            if match:
                description = match.group(1).strip()
                if description:
                    descriptions.append(description)

        if not descriptions:
            await processing_msg.edit_text("Не удалось найти достойных профессий в списке. Видимо, ты обречен.")
            return

        # 4. Выбираем случайное описание
        random_okved_description = random.choice(descriptions)

        # 5. Генерируем саркастический комментарий с помощью ИИ (Gemini)
        prompt = (
            f"Тебе дали описание профессии: '{random_okved_description}'. "
            "Твоя задача - высмеять эту профессию с использованием сарказма и немного бранной лексики, "
            "назвать ее максимально пренебрежительно, но при этом дать актуальное название профессии "
            "или типа деятельности. Сделай это максимально коротко и зло. "
            "Начни с актуального названия или типа деятельности, затем сразу свой комментарий."
            "Используй грубую речь, не стесняйся. Сарказм, унижение. Потоки брани, но чтобы не переборщить."
        )

        ai_response = model.generate_content(prompt) # Используем 'model' из Config.py
        commentary = ai_response.text.strip()

        final_message = (
            f"ОКВЭД: \"{random_okved_description}\"\n"
            f"Моё мнение: {commentary}"
        )
        await processing_msg.edit_text(final_message)

    except requests.exceptions.RequestException as e:
        await processing_msg.edit_text(f"Ой, бля, не могу дотянуться до списка профессий: {e}. Наверное, тебе и так норм живется.")
    except UnicodeDecodeError:
        await processing_msg.edit_text("Ошибка с кодировкой файла ОКВЭД. Похоже, кто-то там накосячил с символами.")
    except Exception as e:
        await processing_msg.edit_text(f"Что-то пошло не так, как всегда. Вот и тут облом: {e}. Видать, ты и правда никому не нужен.")