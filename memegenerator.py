import random
import urllib.parse
import logging
import os
import re
import config

# ================================
# Memegen.link templates
# ================================
MEMEGEN_TEMPLATES = [
    {"id": "drake", "type": "2"},
    {"id": "two-buttons", "type": "2"},
    {"id": "change-my-mind", "type": "1"},
    {"id": "one-does-not-simply", "type": "2"},
    {"id": "waiting-skeleton", "type": "1"},
]


# ================================
# Utils
# ================================

def _escape(text: str) -> str:
    if not text:
        return "..."
    text = text.strip()
    # убираем мусор
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    # memegen плохо переживает длинный unicode
    text = text[:80]
    # пробелы → _
    text = text.replace(" ", "_")
    return urllib.parse.quote(text, safe="")



# ================================
# Memegen core
# ================================

def generate_memegen(template_id: str, text0: str, text1: str | None = None) -> str:
    if not text1:
        text1 = "…"

    return (
        f"https://api.memegen.link/images/"
        f"{template_id}/{_escape(text0)}/{_escape(text1)}.jpg"
    )



# ================================
# Chat history
# ================================

def get_last_chat_messages(chat_id_str: str, limit: int = 10) -> list[str]:
    """Extract last messages from log file for meme context."""

    log_path = config.LOG_FILE
    messages: list[str] = []

    if not os.path.exists(log_path):
        return []

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in reversed(lines):
            match = re.search(r"Chat (\-?\d+).*?\[(.*?)\]: (.*?)$", line)
            if not match:
                continue

            log_chat_id, _, text = match.groups()
            if str(log_chat_id) != str(chat_id_str):
                continue

            text = text.strip()
            if not text:
                continue
            if "мем" in text.lower():
                continue

            messages.append(text)
            if len(messages) >= limit:
                break

        return list(reversed(messages))

    except Exception as e:
        logging.error(f"Log parse error: {e}")
        return []


# ================================
# Public API
# ================================

async def process_meme_command(
    chat_id,
    reply_text: str | None = None,
    history_msgs: list[str] | None = None,
) -> str | None:

    # 1. Базовый текст
    if reply_text:
        base_text = reply_text.strip()
    else:
        if not history_msgs:
            history_msgs = get_last_chat_messages(str(chat_id), limit=10)
        if not history_msgs:
            return None
        base_text = random.choice(history_msgs).strip()

    if not base_text:
        return None

    # 2. Выбор шаблона
    template = random.choice(MEMEGEN_TEMPLATES)

    # 3. Генерация URL
    if template["type"] == "2":
        words = base_text.split()
        if len(words) < 2:
            text0 = base_text
            text1 = "_"
        else:
            mid = len(words) // 2
            text0 = " ".join(words[:mid])
            text1 = " ".join(words[mid:]) or "_"

        return generate_memegen(template["id"], text0, text1)

    # 1-line шаблон
    return generate_memegen(template["id"], base_text, "_")

