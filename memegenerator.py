import random
import requests
import logging

IMGFLIP_USER = "imgflip_hubot"
IMGFLIP_PASS = "imgflip_hubot"

# Базовый набор популярных шаблонов
IMGFLIP_TEMPLATES = [
    {"id": "181913649", "type": "2"},  # Drake
    {"id": "87743020", "type": "2"},   # Two Buttons
    {"id": "129242436", "type": "1"},  # Change My Mind
    {"id": "61579", "type": "2"},      # One Does Not Simply
]

def generate_imgflip(template_id: str, text0: str = "", text1: str = "") -> str | None:
    url = "https://api.imgflip.com/caption_image"
    payload = {
        "template_id": template_id,
        "username": IMGFLIP_USER,
        "password": IMGFLIP_PASS,
        "text0": text0[:300],
        "text1": text1[:300],
    }

    try:
        r = requests.post(url, data=payload, timeout=10)
        data = r.json()
        if not data.get("success"):
            logging.error(f"Imgflip error: {data}")
            return None
        return data["data"]["url"]
    except Exception as e:
        logging.error(f"Imgflip request failed: {e}")
        return None


async def process_meme_command(chat_id, reply_text: str | None = None, history_msgs: list[str] | None = None):
    """
    Генерирует мем через Imgflip.
    Возвращает URL картинки или None.
    """

    # 1. Источник текста
    if reply_text:
        base_text = reply_text.strip()
    else:
        if not history_msgs:
            return None
        base_text = random.choice(history_msgs).strip()

    if not base_text:
        return None

    # 2. Выбор шаблона
    template = random.choice(IMGFLIP_TEMPLATES)

    # 3. Подготовка текста
    if template["type"] == "2":
        # аккуратно делим фразу на 2 части
        words = base_text.split()
        mid = max(1, len(words) // 2)
        text0 = " ".join(words[:mid])
        text1 = " ".join(words[mid:])
    else:
        text0 = base_text
        text1 = ""

    # 4. Генерация
    return generate_imgflip(template["id"], text0, text1)
