#profession.py
import asyncio
import csv
import io
import random

import requests

from core.state import chat_settings
from infrastructure.ai.clients import model, gigachat_model, groq_ai
from prompts import actions

# URL для загрузки файла ОКВЭД
OKVED_URL = "https://classifikators.ru/assets/downloads/okved/okved.csv"


def _decode_okved_csv(content: bytes) -> str:
    """Decode the upstream CSV regardless of whether it is UTF-8 or legacy CP1251."""
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return ""


def _extract_okved_descriptions(content: bytes) -> list[str]:
    decoded_content = _decode_okved_csv(content)
    reader = csv.reader(io.StringIO(decoded_content), delimiter=";")
    descriptions: list[str] = []
    for row in reader:
        if len(row) < 2:
            continue
        description = row[1].strip()
        if description:
            descriptions.append(description)
    return descriptions


async def get_random_okved_and_commentary(message):
    """
    Скачивает случайное описание ОКВЭД, получает саркастичный комментарий от ИИ,
    и отправляет его в чат.
    """
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    processing_msg = await message.reply("Ищу, кем бы тебе стать, обоссанец...")

    try:
        # 1. Скачиваем файл ОКВЭД. Источник исторически менял кодировку,
        # поэтому ниже принимаем и современный UTF-8, и старый CP1251.
        response = await asyncio.to_thread(requests.get, OKVED_URL, timeout=20)
        response.raise_for_status()

        # 2. Разбираем CSV корректным парсером, а не регуляркой по строкам.
        descriptions = _extract_okved_descriptions(response.content)

        if not descriptions:
            await processing_msg.edit_text("Не удалось найти достойных профессий в списке. Видимо, ты обречен.")
            return

        # 3. Выбираем случайное описание
        random_okved_description = random.choice(descriptions)

        # 4. Генерируем саркастический комментарий с помощью выбранной модели
        chat_id = str(message.chat.id)
        current_settings = chat_settings.get(chat_id, {})
        active_model = current_settings.get("active_model", "gemini")

        # Режим истории не подходит
        if active_model == "history":
            active_model = "gemini"

        prompt = (
            f"Тебе дали описание профессии: '{random_okved_description}'. "
            "Твоя задача - высмеять эту профессию с использованием сарказма и немного бранной лексики, "
            "назвать ее максимально пренебрежительно, но при этом дать актуальное название профессии "
            "или типа деятельности. Сделай это максимально коротко и зло. напиши, чем будет заниматься спрашивающий "
            "Начни с актуального названия или типа деятельности, затем сразу свой комментарий."
            "Используй грубую речь, не стесняйся. Сарказм, унижение. Потоки брани, но чтобы не переборщить."
        )

        # Генерируем через активную модель
        def sync_generate():
            if active_model == "gigachat":
                response = gigachat_model.generate_content(prompt, chat_id=int(chat_id))
                return response.text.strip()
            elif active_model == "groq":
                return groq_ai.generate_text(prompt)
            else:  # gemini
                response = model.generate_content(prompt, chat_id=int(chat_id))
                return response.text.strip()

        commentary = await asyncio.to_thread(sync_generate)

        final_message = (
            f"ОКВЭД: \"{random_okved_description}\"\n"
            f"Моё мнение: {commentary}"
        )
        await processing_msg.edit_text(final_message)

    except requests.exceptions.RequestException as e:
        await processing_msg.edit_text(f"Ой, бля, не могу дотянуться до списка профессий: {e}. Наверное, тебе и так норм живется.")
    except UnicodeDecodeError:
        await processing_msg.edit_text("Не смог разобрать кодировку файла ОКВЭД. Источник опять поменял формат.")
    except Exception as e:
        await processing_msg.edit_text(f"Что-то пошло не так, как всегда. Вот и тут облом: {e}. Видать, ты и правда никому не нужен.")
