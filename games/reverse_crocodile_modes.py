"""Extra reverse Crocodile modes: progressive reveal and themed subjects."""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import random
import re
import time

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from PIL import Image, ImageDraw

from core.loader import bot
from games import crocodile
from games import reverse_crocodile as reverse
from games.reverse_crocodile_phrases import mode_label, pick_subject
from games.reverse_crocodile_words import pick_reverse_crocodile_word


REVEAL_INTERVAL_SECONDS = 5
REVEAL_COLS = 5
REVEAL_ROWS = 6
REVEAL_PER_TICK = 3
SURRENDER_DELAY_SECONDS = 5 * 60


def mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🦎 Обычное слово", callback_data="rcrocm_word")],
            [InlineKeyboardButton(text="🧩 Рисунок по кускам", callback_data="rcrocm_reveal")],
            [
                InlineKeyboardButton(text="🎬 Фильм", callback_data="rcrocm_movie"),
                InlineKeyboardButton(text="🐭 Мультфильм", callback_data="rcrocm_cartoon"),
            ],
            [
                InlineKeyboardButton(text="🧠 Пословица", callback_data="rcrocm_proverb"),
                InlineKeyboardButton(text="🗣 Поговорка", callback_data="rcrocm_saying"),
            ],
        ]
    )


def _round_keyboard(chat_id: str, mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏳️ Сдаёмся", callback_data=f"rcrocm_stop_{mode}_{chat_id}")]
        ]
    )


def _again_keyboard(mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Ещё раз", callback_data=f"rcrocm_again_{mode}")],
            [InlineKeyboardButton(text="🎛 Другой режим", callback_data="rcrocm_menu")],
        ]
    )


async def ask_mode(message) -> None:
    await message.answer(
        "🦎 <b>КРАКАДИЛ НАОБОРОТ</b>\nЧего сегодня не умеет рисовать Упупа?",
        parse_mode="HTML",
        reply_markup=mode_keyboard(),
    )


def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", (text or "").casefold())


async def _build_subject_clue(secret: str, chat_id: str, mode: str) -> str | None:
    from AI.summarize import _generate_with_active_model

    kind = mode_label(mode)
    task = f"""Придумай сцену для игры «Крокодил наоборот».
Секрет: {secret}
Тип ответа: {kind}.

Верни ТОЛЬКО описание того, что надо нарисовать, 1–3 коротких предложения.
Нельзя писать секрет, его части, цитаты из него, имена из названия или текстовые подписи.
Никаких букв, вывесок, логотипов и водяных знаков на картинке.
Передай смысл через предметы, действие и абсурдный визуальный гэг.
Для пословицы или поговорки предпочитай смешное буквальное прочтение.
Для фильма или мультфильма используй узнаваемую сцену/набор образов, но не название.
Никакого Markdown и пояснений."""
    secret_compact = _compact(secret)
    for attempt in range(2):
        extra = "\nПредыдущий вариант слишком явно выдал ответ. Придумай другой." if attempt else ""
        try:
            result = await _generate_with_active_model(task + extra, str(chat_id))
        except Exception as exc:
            logging.warning("[rcroc-mode] clue generation failed: %s", exc)
            continue
        clue = " ".join((result or "").split()).strip()
        if not clue:
            continue
        compact = _compact(clue)
        if secret_compact and secret_compact in compact:
            continue
        # Also reject any reasonably long word from a multi-word title/phrase.
        leaked = False
        for token in re.findall(r"[0-9a-zа-яё]+", secret.casefold()):
            if len(token) >= 5 and token in compact:
                leaked = True
                break
        if not leaked:
            return clue[:1400]
    return None


async def _generate_subject_image(secret: str, chat_id: str, mode: str) -> bytes | None:
    from AI import picgeneration as pg
    from AI.gigachat_image import generate_gigachat_image

    clue = await _build_subject_clue(secret, chat_id, mode)
    if not clue:
        return None
    prompt_ru = (
        "Это рисунок-загадка. Изобрази только сцену и не добавляй никакого текста. "
        f"{reverse.IMAGE_STYLE}Сцена: {clue}"
    )
    try:
        image = await generate_gigachat_image(prompt_ru)
        if image:
            return image
        prompt_en = await pg.translate_to_en(prompt_ru)
        for generator in (
            lambda: pg.pollinations_generate(prompt_en),
            lambda: pg.hf_generate(prompt_en, "black-forest-labs/FLUX.1-schnell"),
            lambda: pg.cf_generate_t2i(prompt_en),
        ):
            image = await generator()
            if image:
                return image
    except Exception:
        logging.exception("[rcroc-mode] image generation failed mode=%s", mode)
    return None


def _reveal_order(session: dict) -> list[int]:
    order = session.get("reveal_order")
    if isinstance(order, list) and order:
        return order
    order = list(range(REVEAL_COLS * REVEAL_ROWS))
    random.shuffle(order)
    session["reveal_order"] = order
    return order


def build_reveal_frame(image_bytes: bytes, revealed_tiles: int, *, order: list[int] | None = None) -> bytes:
    """Mask all but N tiles of an image; deterministic order is injectable for tests."""
    source = Image.open(BytesIO(image_bytes)).convert("RGB")
    order = list(order) if order is not None else list(range(REVEAL_COLS * REVEAL_ROWS))
    count = max(0, min(int(revealed_tiles), len(order)))
    visible = set(order[:count])
    masked = Image.new("RGB", source.size, "white")
    draw = ImageDraw.Draw(masked)
    tile_w = source.width / REVEAL_COLS
    tile_h = source.height / REVEAL_ROWS
    for index in visible:
        col = index % REVEAL_COLS
        row = index // REVEAL_COLS
        left = round(col * tile_w)
        top = round(row * tile_h)
        right = round((col + 1) * tile_w)
        bottom = round((row + 1) * tile_h)
        masked.paste(source.crop((left, top, right, bottom)), (left, top))
    # faint grid makes the deliberately hidden fragments visually obvious
    for col in range(1, REVEAL_COLS):
        x = round(col * tile_w)
        draw.line((x, 0, x, source.height), fill=(235, 235, 235), width=1)
    for row in range(1, REVEAL_ROWS):
        y = round(row * tile_h)
        draw.line((0, y, source.width, y), fill=(235, 235, 235), width=1)
    out = BytesIO()
    masked.save(out, format="JPEG", quality=86)
    return out.getvalue()


def _caption(session: dict) -> str:
    label = mode_label(session.get("mode", "word"))
    suffix = "\n🧩 Каждые 5 секунд открывается ещё кусок." if session.get("mode") == "reveal" else ""
    return (
        "🦎 <b>КРАКАДИЛ НАОБОРОТ</b>\n"
        f"Режим: <b>{label}</b>\n"
        f"Угадывайте в чат.{suffix}"
    )


async def _visible_image(session: dict) -> bytes:
    if session.get("mode") != "reveal":
        return session["image"]
    return build_reveal_frame(
        session["image"],
        int(session.get("revealed_tiles", REVEAL_PER_TICK)),
        order=_reveal_order(session),
    )


async def _send_round(chat_id: str, session: dict) -> None:
    image = await _visible_image(session)
    msg = await bot.send_photo(
        int(chat_id),
        BufferedInputFile(image, "rcroc-mode.jpg"),
        caption=_caption(session),
        parse_mode="HTML",
        reply_markup=_round_keyboard(chat_id, session["mode"]),
    )
    session["message_id"] = msg.message_id


async def _reveal_loop(chat_id: str, session: dict) -> None:
    total = REVEAL_COLS * REVEAL_ROWS
    try:
        while reverse.games.get(chat_id) is session and int(session.get("revealed_tiles", 0)) < total:
            await asyncio.sleep(REVEAL_INTERVAL_SECONDS)
            if reverse.games.get(chat_id) is not session:
                return
            session["revealed_tiles"] = min(total, int(session.get("revealed_tiles", 0)) + REVEAL_PER_TICK)
            image = await _visible_image(session)
            try:
                await bot.edit_message_media(
                    chat_id=int(chat_id),
                    message_id=int(session["message_id"]),
                    media=InputMediaPhoto(
                        media=BufferedInputFile(image, "rcroc-reveal.jpg"),
                        caption=_caption(session),
                        parse_mode="HTML",
                    ),
                    reply_markup=_round_keyboard(chat_id, session["mode"]),
                )
            except Exception as exc:
                if "message is not modified" not in str(exc).lower():
                    logging.warning("[rcroc-mode] reveal edit failed: %s", exc)
    except asyncio.CancelledError:
        return


async def _cancel_task(session: dict) -> None:
    task = session.get("mode_task")
    if isinstance(task, asyncio.Task) and not task.done() and task is not asyncio.current_task():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def start_mode(message, mode: str) -> None:
    chat_id = str(message.chat.id)
    if chat_id in reverse.games:
        await message.answer("🦎 Раунд уже идёт.")
        return
    if mode == "word":
        await reverse.ask_difficulty(message)
        return

    secret = pick_reverse_crocodile_word("medium") if mode == "reveal" else pick_subject(mode)
    status = await message.answer(f"🦎 Режим «{mode_label(mode)}»: рисую всратый шедевр...")
    image = (
        await reverse._generate_word_image(secret, chat_id)
        if mode == "reveal"
        else await _generate_subject_image(secret, chat_id, mode)
    )
    if not image:
        await status.edit_text("Не смог нарисовать, у меня лапки. Попробуйте ещё раз.")
        return

    session = {
        "word": secret,
        "difficulty": "medium",
        "mode": mode,
        "image": image,
        "message_id": None,
        "started_at": time.monotonic(),
        "mode_task": None,
        "revealed_tiles": REVEAL_PER_TICK,
        "reveal_order": [],
    }
    reverse.games[chat_id] = session
    try:
        await _send_round(chat_id, session)
        if mode == "reveal":
            session["mode_task"] = crocodile._start_background_task(
                _reveal_loop(chat_id, session), name=f"reverse-croc-reveal:{chat_id}"
            )
    except Exception:
        reverse.games.pop(chat_id, None)
        logging.exception("[rcroc-mode] failed to start mode=%s", mode)
        await status.edit_text("Не смог запустить раунд.")
        return
    await status.delete()


async def finish_mode(chat_id: str, session: dict, text: str) -> None:
    if reverse.games.get(chat_id) is session:
        reverse.games.pop(chat_id, None)
    await _cancel_task(session)
    await bot.send_message(
        int(chat_id), text, parse_mode="HTML", reply_markup=_again_keyboard(session.get("mode", "reveal"))
    )
    await bot.send_message(
        int(chat_id),
        crocodile.format_leaderboard(chat_id, "🏆 Самые умные педорасы"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def check_answer(message) -> bool:
    chat_id = str(message.chat.id)
    session = reverse.games.get(chat_id)
    if not session or session.get("mode") in (None, "word") or not message.text:
        return False
    if not crocodile._contains_answer(message.text, session.get("word", "")):
        return False
    if message.from_user:
        crocodile.add_point(chat_id, message.from_user.id, message.from_user.full_name)
    winner = message.from_user.full_name if message.from_user else "Кто-то"
    secret = str(session["word"]).upper()
    await finish_mode(chat_id, session, f"🎉 <b>{winner}</b> угадал! Ответ: <b>{secret}</b>.")
    return True


async def handle_callback(callback) -> None:
    data = callback.data or ""
    if data == "rcrocm_menu":
        await callback.answer()
        await ask_mode(callback.message)
        return
    if data == "rcrocm_word":
        await callback.answer()
        await reverse.ask_difficulty(callback.message)
        return
    if data.startswith("rcrocm_again_"):
        mode = data[len("rcrocm_again_"):]
        await callback.answer("Рисую новое...")
        await start_mode(callback.message, mode)
        return
    if data.startswith("rcrocm_") and not data.startswith("rcrocm_stop_"):
        mode = data[len("rcrocm_"):]
        if mode in {"reveal", "movie", "cartoon", "proverb", "saying"}:
            await callback.answer("Рисую...")
            await start_mode(callback.message, mode)
            return

    if data.startswith("rcrocm_stop_"):
        rest = data[len("rcrocm_stop_"):]
        mode, _, chat_id = rest.rpartition("_")
        session = reverse.games.get(chat_id)
        if not session or session.get("mode") != mode:
            await callback.answer("Игра уже закончилась")
            return
        elapsed = time.monotonic() - float(session.get("started_at") or 0)
        remaining = max(0, int(SURRENDER_DELAY_SECONDS - elapsed + 0.999))
        if remaining:
            minutes, seconds = divmod(remaining, 60)
            await callback.answer(f"Сдаться можно через {minutes}:{seconds:02d}.", show_alert=True)
            return
        await callback.answer("Слабаки")
        await finish_mode(
            chat_id,
            session,
            f"🏳️ Сдались? Ответ: <b>{str(session['word']).upper()}</b>.",
        )
