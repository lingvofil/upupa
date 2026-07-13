# === AI/chat_recall.py — работа с памятью чата и фактчек ===
#
# Три функции на базе лога сообщений (LOG_FILE):
#  - "упупа когда мы говорили про X" — поиск эпизодов в истории чата + AI-сводка с датами
#  - "упупа рассуди" (реплаем на спор или просто в чат) — вердикт по перепалке,
#    промпт тот же, что у "чотам" (PROMPTS_MEDIA)
#  - "пиздиш" (реплаем) — фактчек утверждения через веб-поиск (services.web_context)

import asyncio
import logging
import random
import re
from datetime import datetime

from aiogram import types
from thefuzz import fuzz

from config import LOG_FILE
from prompts import PROMPTS_MEDIA
from core.upupa_utils import normalize_upupa_command
from AI.summarize import _generate_with_active_model
from services.web_context import get_web_context

# --- Параметры поиска по истории ---
MATCH_THRESHOLD = 60   # минимальная схожесть (0-100) для попадания в эпизод
MAX_EPISODES = 5       # сколько эпизодов показывать модели
CONTEXT_WINDOW = 3     # сколько сообщений контекста брать вокруг совпадения
MIN_HISTORY = 10       # меньше сообщений в логе — искать не в чем

_LOG_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) - Chat (\-?\d+) \((.*?)\) - User (\d+) \((.*?)\) \[(.*?)\]: (.*)$"
)


def _read_chat_log(chat_id: str) -> list[dict]:
    """Все сообщения чата из лога: [{dt, name, text}], в хронологическом порядке."""
    out = []
    chat_id = str(chat_id)
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if chat_id not in line:
                    continue
                m = _LOG_RE.search(line)
                if not m:
                    continue
                ts, log_chat_id, _chat, _uid, username, display, text = m.groups()
                if log_chat_id != chat_id or not text.strip():
                    continue
                try:
                    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f")
                except ValueError:
                    continue
                name = (display or "").strip() or username
                out.append({"dt": dt, "name": name, "text": text.strip()})
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.error(f"[chat_recall] не смог прочитать лог: {e}")
    return out


# ================== "УПУПА КОГДА МЫ ГОВОРИЛИ ПРО" ==================

def _find_episodes(messages: list[dict], query: str) -> list[list[dict]]:
    """Ищет эпизоды обсуждения темы: топ совпадений + контекст вокруг каждого."""
    query_l = query.lower()
    scored = []
    for i, msg in enumerate(messages):
        text_l = msg["text"].lower()
        if query_l in text_l:
            score = 100
        else:
            score = fuzz.token_set_ratio(query_l, text_l)
        if score >= MATCH_THRESHOLD:
            scored.append((score, i))

    scored.sort(reverse=True)

    picked_idx: list[int] = []
    for _score, i in scored:
        if len(picked_idx) >= MAX_EPISODES:
            break
        # не берём совпадение, попадающее в уже выбранный эпизод
        if any(abs(i - j) <= CONTEXT_WINDOW * 2 for j in picked_idx):
            continue
        picked_idx.append(i)

    picked_idx.sort()
    episodes = []
    for i in picked_idx:
        lo = max(0, i - CONTEXT_WINDOW)
        hi = min(len(messages), i + CONTEXT_WINDOW + 1)
        episodes.append(messages[lo:hi])
    return episodes


def _extract_recall_topic(text: str) -> str:
    """'упупа когда мы говорили про кино?' -> 'кино'"""
    norm = normalize_upupa_command(text)
    tail = norm.split("говорили", 1)[1] if "говорили" in norm else ""
    tail = re.sub(r"^\s*(про|обо|об|о)\s+", "", tail.strip())
    return tail.strip(" ?!.,")


async def process_recall_command(message: types.Message):
    topic = _extract_recall_topic(message.text or "")
    if not topic:
        await message.reply("Про что говорили-то? Пиши: упупа когда мы говорили про [тему]")
        return

    chat_id = str(message.chat.id)
    status = await message.reply("Копаюсь в ваших грязных архивах...")

    messages = await asyncio.to_thread(_read_chat_log, chat_id)
    if len(messages) < MIN_HISTORY:
        await status.edit_text("У меня еще слишком мало компромата на этот чат.")
        return

    episodes = await asyncio.to_thread(_find_episodes, messages, topic)
    if not episodes:
        await status.edit_text(f"Хуй там. Про «{topic}» вы никогда не говорили. Или говорили так убого, что я не запомнил.")
        return

    fragments = []
    for ep in episodes:
        date_str = ep[0]["dt"].strftime("%d.%m.%Y")
        fragments.append(f"--- {date_str} ---")
        fragments.extend(f"{m['name']}: {m['text']}" for m in ep)
    fragments_text = "\n".join(fragments)

    prompt = (
        f"В чате искали, когда обсуждали тему «{topic}». Ниже найденные фрагменты переписки с датами.\n"
        f"Ответь: когда об этом говорили (назови даты) и кратко перескажи, что именно говорили и кто. "
        f"Сильно иронизируй, используй сарказм и нецензурную лексику, не более 120 слов. "
        f"Если какой-то фрагмент не про тему — игнорируй его.\n\n"
        f"Фрагменты:\n{fragments_text}\n\nОтвет:"
    )

    try:
        answer = await _generate_with_active_model(prompt, chat_id)
        await status.edit_text(answer)
    except Exception as e:
        logging.error(f"[chat_recall] recall error: {e}")
        await status.edit_text("Память отшибло. Попробуй позже.")


# ================== "УПУПА РАССУДИ" ==================

VERDICT_CONTEXT_RADIUS = 15   # сообщений вокруг реплайнутого
VERDICT_FALLBACK_TAIL = 30    # без реплая берём хвост переписки
VERDICT_MAX_GAP_SECONDS = 300  # разрыв во времени, после которого считаем, что начался другой разговор


def _trim_to_same_conversation(messages: list[dict], center: int) -> list[dict]:
    """Обрезает окно по времени: как только между соседними сообщениями
    разрыв больше VERDICT_MAX_GAP_SECONDS, считаем, что дальше уже другой
    диалог, и туда не заходим (даже если позиционно это в радиусе)."""
    lo = center
    while lo > 0 and (messages[lo]["dt"] - messages[lo - 1]["dt"]).total_seconds() <= VERDICT_MAX_GAP_SECONDS:
        lo -= 1
    hi = center
    while hi < len(messages) - 1 and (messages[hi + 1]["dt"] - messages[hi]["dt"]).total_seconds() <= VERDICT_MAX_GAP_SECONDS:
        hi += 1
    return messages[lo:hi + 1]


def _build_dispute_context(messages: list[dict], target_text: str) -> list[dict]:
    """Контекст спора: окно вокруг реплайнутого сообщения либо хвост чата."""
    if target_text:
        target_text = target_text.strip()
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["text"] == target_text:
                lo = max(0, i - VERDICT_CONTEXT_RADIUS)
                hi = min(len(messages), i + VERDICT_CONTEXT_RADIUS + 1)
                window = messages[lo:hi]
                # индекс целевого сообщения внутри window
                center = i - lo
                return _trim_to_same_conversation(window, center)
    return messages[-VERDICT_FALLBACK_TAIL:]


async def process_verdict_command(message: types.Message):
    chat_id = str(message.chat.id)
    status = await message.reply("Надеваю мантию судьи...")

    target_text = ""
    if message.reply_to_message:
        target_text = message.reply_to_message.text or message.reply_to_message.caption or ""

    messages = await asyncio.to_thread(_read_chat_log, chat_id)
    context = _build_dispute_context(messages, target_text)

    if not context and target_text and message.reply_to_message.from_user:
        context = [{"name": message.reply_to_message.from_user.full_name, "text": target_text, "dt": datetime.now()}]
    if not context:
        await status.edit_text("Тут и судить нечего, вы даже поругаться нормально не можете.")
        return

    dialog_text = "\n".join(f"{m['name']}: {m['text']}" for m in context)

    # Тот же промпт, что у "чотам" (PROMPTS_MEDIA), + задача судьи
    base_prompt = f"{random.choice(PROMPTS_MEDIA)}, не более 80 слов"
    prompt = (
        f"Ниже спор (или просто перепалка) из чата. Рассуди его: реши, кто прав, кто несёт чушь, "
        f"и вынеси однозначный вердикт с именем победителя. {base_prompt}\n\n"
        f"Спор:\n{dialog_text}\n\nВердикт:"
    )

    try:
        answer = await _generate_with_active_model(prompt, chat_id)
        await status.edit_text(answer)
    except Exception as e:
        logging.error(f"[chat_recall] verdict error: {e}")
        await status.edit_text("Суд удаляется на совещание (всё сломалось).")


# ================== "ПИЗДИШ" (ФАКТЧЕК) ==================

async def process_factcheck_command(message: types.Message):
    if not message.reply_to_message:
        await message.reply("Реплайни на сообщение, которое проверить.")
        return

    claim = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    if not claim:
        await message.reply("Тут нечего проверять, тут даже текста нет.")
        return

    chat_id = str(message.chat.id)
    author = message.reply_to_message.from_user.full_name if message.reply_to_message.from_user else "автор"
    status = await message.reply("Проверяю на детекторе пиздежа...")

    web_block = ""
    try:
        web_block = await get_web_context(claim[:200])
    except Exception as e:
        logging.warning(f"[chat_recall] factcheck web search failed: {e}")

    if not web_block:
        web_block = "\n(Веб-поиск ничего не дал — оценивай по своим знаниям и здравому смыслу.)"

    prompt = (
        f"Проверь утверждение пользователя {author} на правдивость.\n"
        f"Утверждение: «{claim}»\n{web_block}\n"
        f"Начни ответ строго с одного вердикта: «ПИЗДЁЖ», «ПРАВДА» или «ПОЛУПРАВДА». "
        f"Затем в 2-3 предложениях объясни почему, с сарказмом и нецензурной лексикой, не более 80 слов. "
        f"Если утверждение — мнение или шутка, а не факт, так и скажи."
    )

    try:
        answer = await _generate_with_active_model(prompt, chat_id)
        await status.edit_text(answer)
    except Exception as e:
        logging.error(f"[chat_recall] factcheck error: {e}")
        await status.edit_text("Детектор пиздежа перегорел. Наверное, от перегрузки.")
