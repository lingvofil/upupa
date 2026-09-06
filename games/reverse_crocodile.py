# === games/reverse_crocodile.py — "кракадил наоборот" ===
#
# Обратный кракадил: бот загадывает одно слово, сам рисует и чат угадывает.
# Очки угадывающих общие с обычным кракадилом; рисование идёт через тот же
# GigaChat-first waterfall, что и современные image-фичи.

import logging
import random

from aiogram import types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from core.loader import bot
from games.crocodile import _contains_answer, _normalize_guess, add_point, format_leaderboard
from games.crocodile_single_words import pick_single_crocodile_word

# Намеренно не делаем «красивую нейросетевую картинку»: это должна быть
# смешная рисовалка, которую интересно разгадывать.
IMAGE_STYLE = (
    "Нарисуй как неумелый человек фломастерами или восковыми мелками на белой бумаге: "
    "простые плоские формы, немного кривые линии, неровные контуры, минимум деталей, без реализма, "
    "без 3D, без глянца, без кинематографического света и без дизайнерской полировки. "
    "Один главный визуальный гэг. Если для слова естественно получается визуальный каламбур, "
    "буквальное смешное прочтение или игра значений — используй её, но картинка всё равно должна помогать угадать исходное слово. "
    "Не добавляй текст, буквы, слова, подписи, вывески, логотипы или водяные знаки. "
)

MAX_HINTS = 3

# chat_id(str) -> {"word": str, "hints": int, "image": bytes}
games: dict[str, dict] = {}


def _keyboard(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💡 Подсказка", callback_data=f"rcroc_hint_{chat_id}"),
                InlineKeyboardButton(text="🔄 Другая картинка", callback_data=f"rcroc_img_{chat_id}"),
            ],
            [InlineKeyboardButton(text="🏳️ Сдаёмся", callback_data=f"rcroc_stop_{chat_id}")],
        ]
    )


def _again_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔁 Ещё раз", callback_data="rcroc_again_0")]]
    )


async def _generate_word_image(word: str) -> bytes | None:
    """GigaChat first, then the same reserve providers used by image generation."""
    from AI import picgeneration as pg
    from AI.gigachat_image import generate_gigachat_image

    prompt_ru = (
        f"Это игра в Крокодила. Нужно изобразить ОДНО загадное слово «{word}», не печатая его на картинке. "
        f"{IMAGE_STYLE}"
    )
    try:
        image = await generate_gigachat_image(prompt_ru)
        if image:
            logging.info("[rcroc] image provider=gigachat word=%s", word)
            return image

        prompt_en = await pg.translate_to_en(prompt_ru)
        image = await pg.pollinations_generate(prompt_en)
        if image:
            logging.info("[rcroc] image provider=pollinations word=%s", word)
            return image

        image = await pg.hf_generate(prompt_en, "black-forest-labs/FLUX.1-schnell")
        if image:
            logging.info("[rcroc] image provider=huggingface word=%s", word)
            return image

        image = await pg.cf_generate_t2i(prompt_en)
        if image:
            logging.info("[rcroc] image provider=cloudflare word=%s", word)
            return image
    except Exception as exc:
        logging.warning("[rcroc] image waterfall failed word=%s: %s", word, exc, exc_info=True)
    return None


def _make_hint(word: str, hint_number: int) -> str:
    """1 — длина, 2 — первая буква, 3 — половина букв."""
    if hint_number == 1:
        return f"💡 В слове {len(word)} букв(ы)."
    if hint_number == 2:
        return f"💡 Начинается на «{word[0].upper()}»."
    letters = list(word)
    positions = [i for i in range(1, len(letters)) if letters[i] != " "]
    random.shuffle(positions)
    hidden = set(positions[: max(1, len(positions) // 2)])
    masked = " ".join(
        "▪️" if i in hidden else letters[i].upper()
        for i in range(len(letters))
    )
    return f"💡 Ладно, держите: {masked}"


async def start_game(message: types.Message):
    chat_id = str(message.chat.id)
    word = pick_single_crocodile_word()

    status = await message.answer("🦎 КРАКАДИЛ НАОБОРОТ\nЗагадал слово, рисую свой шедевр...")
    image = await _generate_word_image(word)
    if not image:
        await status.edit_text("Не смог нарисовать, у меня лапки. Попробуй ещё раз.")
        return

    games[chat_id] = {
        "word": word,
        "hints": 0,
        "image": image,
    }
    await status.delete()
    await bot.send_photo(
        chat_id=int(chat_id),
        photo=BufferedInputFile(image, "rcroc.png"),
        caption="🦎 <b>КРАКАДИЛ НАОБОРОТ</b>\nТеперь рисую я, а вы угадываете. Пишите варианты в чат!",
        parse_mode="HTML",
        reply_markup=_keyboard(chat_id),
    )
    logging.info("[rcroc] start chat=%s word=%s", chat_id, word)


async def _finish_game(chat_id: str, text: str):
    games.pop(chat_id, None)
    await bot.send_message(
        int(chat_id), text, parse_mode="HTML", reply_markup=_again_keyboard()
    )
    await bot.send_message(
        int(chat_id),
        format_leaderboard(chat_id, "🏆 Самые умные педорасы"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def handle_callback(cb: types.CallbackQuery):
    data = cb.data or ""

    if data.startswith("rcroc_again"):
        await cb.answer("Рисую новое...")
        await start_game(cb.message)
        return

    chat_id = data.split("_")[-1]
    session = games.get(chat_id)
    if not session:
        await cb.answer("Игра уже закончилась")
        return

    if data.startswith("rcroc_hint_"):
        if session["hints"] >= MAX_HINTS:
            await cb.answer("Хватит с вас подсказок, думайте!", show_alert=True)
            return
        session["hints"] += 1
        hint = _make_hint(session["word"], session["hints"])
        await cb.answer()
        await bot.send_message(int(chat_id), hint)

    elif data.startswith("rcroc_img_"):
        await cb.answer("Рисую то же самое, но по-другому...")
        image = await _generate_word_image(session["word"])
        if not games.get(chat_id) or games[chat_id]["word"] != session["word"]:
            return
        if not image:
            await bot.send_message(int(chat_id), "Вторая попытка не удалась, смотрите первую.")
            return
        session["image"] = image
        await bot.send_photo(
            chat_id=int(chat_id),
            photo=BufferedInputFile(image, "rcroc2.png"),
            caption="🎨 Вот вам другой ракурс, слово то же.",
            reply_markup=_keyboard(chat_id),
        )

    elif data.startswith("rcroc_stop_"):
        word = session["word"]
        await cb.answer("Слабаки")
        await _finish_game(chat_id, f"🏳️ Сдались? Это был(а) <b>{word.upper()}</b>. Позорище.")


async def check_answer(msg: types.Message) -> bool:
    """Вызывается из catch-all. True — сообщение было верным ответом."""
    chat_id = str(msg.chat.id)
    session = games.get(chat_id)
    if not session or not msg.text:
        return False

    word = _normalize_guess(session["word"])
    if not _contains_answer(msg.text, session["word"]):
        return False

    if msg.from_user:
        add_point(chat_id, msg.from_user.id, msg.from_user.full_name)
    winner = msg.from_user.full_name if msg.from_user else "Кто-то"
    await _finish_game(
        chat_id,
        f"🎉 <b>{winner}</b> угадал! Это был(а) <b>{word.upper()}</b>.\nА я неплохо рисую, да?",
    )
    return True
