# === AI/comic.py — комикс по событиям чата ===
#
# Команда "комикс" / "упупа комикс": берём переписку за 12 часов,
# AI пишет сценарий из 4 панелей (сцена на английском для генератора картинок +
# подпись на русском), генерируем 4 картинки через GigaChat-2 и склеиваем
# лист 2x2 с подписями через Pillow.

import asyncio
import json
import logging
import re
import textwrap
from datetime import datetime, timedelta
from io import BytesIO

from aiogram import types
from aiogram.types import BufferedInputFile
from PIL import Image, ImageDraw, ImageFont

from core.paths import USER_MESSAGES_LOG_PATH as LOG_FILE
from AI.summarize import _get_chat_messages, _generate_with_active_model
from AI.gigachat_image import generate_gigachat_image

COMIC_HOURS = 12
MIN_MESSAGES = 5
MAX_SCRIPT_CHARS = 8000
COMIC_HISTORY_SAMPLE_MESSAGES = 3000
COMIC_HISTORY_RECENT_MESSAGES = 1000

PANEL = 512
CAP_H = 100
MARGIN = 10
FONT_PATH = "assets/fonts/BerlinSansFBCyrillic-Regular.ttf"

PANEL_STYLE = "comic book panel, cartoon style, bold outlines, flat colors, no text, no speech bubbles, no letters"

COMIC_SCRIPT_PROMPT = """Ты сценарист комиксов. По переписке из чата ниже придумай смешной комикс ровно из 4 панелей про реальные события и темы этой переписки.
Формат вывода — строго JSON без markdown-блоков и пояснений:
{"panels": [{"scene": "...", "caption": "..."}, {"scene": "...", "caption": "..."}, {"scene": "...", "caption": "..."}, {"scene": "...", "caption": "..."}]}
Правила:
- scene — описание картинки НА АНГЛИЙСКОМ для генератора изображений: кто в кадре (описывай персонажей внешне, без имён), что делают, обстановка, эмоции.
- caption — подпись НА РУССКОМ: реплика персонажа или закадровый текст, до 12 слов, с иронией и сарказмом, можно с матом, имена участников использовать можно и нужно.
- Панели должны складываться в историю с завязкой и панчлайном в четвёртой.

Переписка:
{messages}
"""


async def _generate_panel_image(scene: str) -> bytes | None:
    """Генерирует одну панель через тот же GigaChat text-to-image, что и «нарисуй»."""
    return await generate_gigachat_image(f"{PANEL_STYLE}, {scene}")


def _parse_panels(raw: str) -> list[dict]:
    """Достаёт из ответа модели список из 4 панелей {scene, caption}."""
    if not raw:
        return []
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logging.error(f"[comic] JSON parse failed: {e}; raw: {raw[:300]}")
        return []
    panels = data.get("panels") if isinstance(data, dict) else None
    if not isinstance(panels, list):
        return []
    result = []
    for p in panels[:4]:
        if isinstance(p, dict) and p.get("scene"):
            result.append({"scene": str(p["scene"]), "caption": str(p.get("caption", ""))})
    return result if len(result) == 4 else []


def _load_font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _build_recent_dialog_text(messages: list[dict], max_chars: int = MAX_SCRIPT_CHARS) -> str:
    """Собрать хвост диалога без промежуточной гигантской строки."""
    selected_reversed: list[str] = []
    used = 0
    for message in reversed(messages):
        line = f"{message['display_name']}: {message['text']}"
        if not selected_reversed and len(line) > max_chars:
            return line[-max_chars:]
        extra = len(line) + (1 if selected_reversed else 0)
        if used + extra > max_chars:
            break
        selected_reversed.append(line)
        used += extra
    return "\n".join(reversed(selected_reversed))


def _compose_comic(images: list, captions: list[str]) -> bytes:
    """Склеивает 4 панели в лист 2x2 с подписями под каждой."""
    width = 2 * PANEL + 3 * MARGIN
    height = 2 * (PANEL + CAP_H) + 3 * MARGIN
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = _load_font(26)
    placeholder_font = _load_font(120)

    for idx in range(4):
        row, col = divmod(idx, 2)
        x = MARGIN + col * (PANEL + MARGIN)
        y = MARGIN + row * (PANEL + CAP_H + MARGIN)

        img_bytes = images[idx] if idx < len(images) else None
        if img_bytes:
            with Image.open(BytesIO(img_bytes)) as source:
                panel = source.convert("RGB").resize((PANEL, PANEL))
            sheet.paste(panel, (x, y))
            panel.close()
        else:
            draw.rectangle([x, y, x + PANEL, y + PANEL], fill="#dddddd")
            draw.text((x + PANEL // 2 - 30, y + PANEL // 2 - 70), "?", font=placeholder_font, fill="black")
        draw.rectangle([x, y, x + PANEL - 1, y + PANEL - 1], outline="black", width=4)

        caption = captions[idx] if idx < len(captions) else ""
        lines = textwrap.wrap(caption, width=40)[:3]
        text_y = y + PANEL + 6
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            draw.text((x + (PANEL - line_w) // 2, text_y), line, font=font, fill="black")
            text_y += 30

    buf = BytesIO()
    sheet.save(buf, "PNG")
    sheet.close()
    return buf.getvalue()


async def process_comic_command(message: types.Message):
    chat_id = str(message.chat.id)
    status = await message.reply("Рисую комикс про вас... это надолго, материала много.")

    time_threshold = datetime.now() - timedelta(hours=COMIC_HOURS)
    messages, _users, chat_name = await asyncio.to_thread(
        _get_chat_messages,
        LOG_FILE,
        chat_id,
        time_threshold,
        COMIC_HISTORY_SAMPLE_MESSAGES,
        COMIC_HISTORY_RECENT_MESSAGES,
    )
    if not messages or len(messages) < MIN_MESSAGES:
        await status.edit_text(f"За последние {COMIC_HOURS} часов нихуя не произошло. Комикс про пустоту рисовать не буду.")
        return

    dialog_text = _build_recent_dialog_text(messages)

    try:
        raw_script = await _generate_with_active_model(
            COMIC_SCRIPT_PROMPT.replace("{messages}", dialog_text), chat_id
        )
    except Exception as e:
        logging.error(f"[comic] script generation failed: {e}")
        await status.edit_text("Сценарист запил, комикса не будет.")
        return

    panels = _parse_panels(raw_script)
    if not panels:
        await status.edit_text("Сценарий комикса вышел настолько плохим, что я его выбросил. Попробуй ещё раз.")
        return

    images = []
    for i, panel in enumerate(panels):
        try:
            await status.edit_text(f"Рисую панель {i + 1}/4...")
        except Exception:
            pass
        img = await _generate_panel_image(panel["scene"])
        images.append(img)
        if i < len(panels) - 1:
            await asyncio.sleep(2)

    if not any(images):
        await status.edit_text("Художник тоже запил. Ни одной панели не нарисовалось.")
        return

    sheet = await asyncio.to_thread(_compose_comic, images, [p["caption"] for p in panels])
    await status.delete()
    title = chat_name or "этом чате"
    await message.reply_photo(
        BufferedInputFile(sheet, "comic.png"),
        caption=f"📓 Комикс: как прошли последние {COMIC_HOURS} часов в «{title}»",
    )