# === AI/translate.py — команда "переведи" ===
#
# Реплаем на сообщение (или на подпись к медиа):
#   "переведи"            — иностранный текст -> русский,
#                           русский текст -> блатной жаргон (феня)
#   "переведи на [язык]"  — перевод на указанный язык; выдуманные языки
#                           ("собачий", "орочий") имитируются звукоподражанием

import logging
import re

from aiogram import types

from config import bot

# Порог: если кириллицы больше, чем латиницы — считаем текст русским
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)

PROMPT_TO_RUSSIAN = (
    "Переведи следующий текст на русский язык. "
    "В ответе только перевод, без пояснений и вступлений.\n\nТекст: {text}"
)

PROMPT_TO_FENYA = (
    "Переведи следующий текст на блатной жаргон (феня, тюремно-криминальный сленг). "
    "Сохрани смысл, но перескажи так, как сказал бы матёрый уголовник с богатым словарным запасом фени. "
    "В ответе только перевод, без пояснений и вступлений.\n\nТекст: {text}"
)

PROMPT_TO_LANGUAGE = (
    "Переведи следующий текст на язык: «{language}».\n"
    "Если это реальный язык — сделай точный перевод на него.\n"
    "Если такого языка не существует (выдуманный или шуточный, например «собачий», «кошачий», «орочий») — "
    "сымитируй его максимально правдоподобно: используй звукоподражания, характерные звуки и выдуманные слова, "
    "чтобы результат выглядел как настоящий текст на этом языке "
    "(например, на собачьем языке это будет гавканье: гав-гав, вуф, р-р-р).\n"
    "В ответе только перевод, без пояснений и вступлений.\n\nТекст: {text}"
)


def is_translate_command(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return low == "переведи" or low.startswith("переведи ")


def _extract_target_language(command_text: str) -> str | None:
    """'переведи на испанский' -> 'испанский'; None — если язык не указан."""
    rest = command_text[len("переведи"):].strip()
    if rest.lower().startswith("на ") and rest[3:].strip():
        return rest[3:].strip()
    return None


def _looks_russian(text: str) -> bool:
    return len(_CYRILLIC_RE.findall(text)) > len(_LATIN_RE.findall(text))


async def process_translate_command(message: types.Message) -> None:
    # Ленивый импорт: избегаем цикла AI.translate <-> AI.talking
    from AI.talking import generate_simple_response

    source_message = message.reply_to_message
    source_text = None
    if source_message:
        source_text = source_message.text or source_message.caption

    if not source_text:
        await message.reply("Ответь командой «переведи» на сообщение с текстом.")
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    target_language = _extract_target_language(message.text)
    if target_language:
        prompt = PROMPT_TO_LANGUAGE.format(language=target_language, text=source_text)
    elif _looks_russian(source_text):
        prompt = PROMPT_TO_FENYA.format(text=source_text)
    else:
        prompt = PROMPT_TO_RUSSIAN.format(text=source_text)

    try:
        response_text = await generate_simple_response(prompt, str(message.chat.id))
        await message.reply(response_text)
    except Exception as e:
        logging.error(f"process_translate_command: {e}", exc_info=True)
        await message.reply("Переводчик сломался, попробуй ещё раз.")
