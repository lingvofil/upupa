# === games/reverse_crocodile.py — "кракадил наоборот" ===
#
# Обратный кракадил: бот загадывает одно слово, сам рисует и чат угадывает.
# Очки угадывающих общие с обычным кракадилом; рисование идёт через тот же
# GigaChat-first waterfall, что и современные image-фичи.

import logging
import random
import re
import time

from aiogram import types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from core.loader import bot
from games.crocodile import _contains_answer, _normalize_guess, add_point, format_leaderboard
from games.reverse_crocodile_words import (
    difficulty_label,
    normalize_difficulty,
    pick_reverse_crocodile_word,
)

# Намеренно не делаем «красивую нейросетевую картинку»: это должна быть
# смешная рисовалка, которую интересно разгадывать.
IMAGE_STYLE = (
    "Нарисуй как неумелый человек фломастерами или восковыми мелками на белой бумаге: "
    "простые плоские формы, немного кривые линии, неровные контуры, минимум деталей, без реализма, "
    "без 3D, без глянца, без кинематографического света и без дизайнерской полировки. "
    "Один главный визуальный гэг. Если естественно получается визуальный каламбур, буквальное смешное прочтение "
    "или игра значений — используй её. "
    "На изображении не должно быть вообще никакого читаемого текста: никаких букв, слов, подписей, вывесок, "
    "этикеток, логотипов или водяных знаков. Если предмет обычно содержит надпись, оставь это место пустым. "
)

MAX_HINTS = 3
SURRENDER_DELAY_SECONDS = 5 * 60

# chat_id(str) -> {"word": str, "difficulty": str, "hints": int, "image": bytes, "started_at": float}
games: dict[str, dict] = {}


def _keyboard(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💡 Подсказка", callback_data=f"rcroc_hint_{chat_id}")],
            [InlineKeyboardButton(text="🏳️ Сдаёмся", callback_data=f"rcroc_stop_{chat_id}")],
        ]
    )


def _difficulty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 Легко", callback_data="rcroc_level_easy"),
                InlineKeyboardButton(text="🟡 Средне", callback_data="rcroc_level_medium"),
                InlineKeyboardButton(text="🔴 Сложно", callback_data="rcroc_level_hard"),
            ]
        ]
    )


def _again_keyboard(difficulty: str) -> InlineKeyboardMarkup:
    difficulty = normalize_difficulty(difficulty)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Ещё раз", callback_data=f"rcroc_again_{difficulty}")],
            [InlineKeyboardButton(text="🎚 Сменить сложность", callback_data="rcroc_choose_level")],
        ]
    )


def callback_difficulty(data: str | None) -> str:
    """Extract difficulty from level/restart callbacks; old buttons fall back to medium."""
    value = (data or "").rsplit("_", 1)[-1]
    return normalize_difficulty(value)


async def ask_difficulty(message: types.Message) -> None:
    await message.answer(
        "🦎 <b>КРАКАДИЛ НАОБОРОТ</b>\nВыбирайте, насколько сильно хотите страдать:",
        parse_mode="HTML",
        reply_markup=_difficulty_keyboard(),
    )


def _compact_letters(text: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", (text or "").casefold())


def _clue_leaks_secret(clue: str, word: str) -> bool:
    """Reject a scene description if it still contains the answer or its obvious stem."""
    secret = _compact_letters(word)
    return bool(secret and secret in _compact_letters(clue))


def _prompt_contains_literal_secret(prompt: str, word: str) -> bool:
    """Check the final image prompt for the literal answer as a standalone token."""
    secret = (word or "").strip().casefold()
    if not secret:
        return False
    pattern = rf"(?<![0-9a-zа-яё]){re.escape(secret)}(?![0-9a-zа-яё])"
    return re.search(pattern, (prompt or "").casefold()) is not None


async def _build_visual_clue(word: str, chat_id: str) -> str | None:
    """Turn the answer into a scene description before any image model sees it."""
    from AI.summarize import _generate_with_active_model

    task = f"""Ты придумываешь рисунок для игры «Крокодил наоборот».
Секретное однословное русское слово: {word}

Верни ТОЛЬКО описание визуальной сцены для художника, 1–2 коротких предложения.
Критически важно:
- не пиши секретное слово и не используй его как подпись, название или часть текста;
- по возможности не используй однокоренные формы этого слова;
- никаких букв, надписей, этикеток, вывесок, логотипов и письменных подсказок внутри сцены;
- показывай смысл через предметы, действие, форму и ситуацию;
- если можно придумать понятный визуальный каламбур или буквальное смешное прочтение — предпочти его;
- рисунок должен оставаться угадываемым, но не выдавать ответ напрямую.

Никаких пояснений, кавычек и Markdown — только то, что нужно нарисовать."""

    for attempt in range(2):
        retry = "\nПредыдущая версия выдала секрет. Перефразируй через другие предметы и действия." if attempt else ""
        try:
            clue = await _generate_with_active_model(task + retry, str(chat_id))
        except Exception as exc:
            logging.warning("[rcroc] visual clue generation failed word=%s: %s", word, exc)
            continue
        clean = " ".join((clue or "").split()).strip()
        if not clean:
            continue
        if _clue_leaks_secret(clean, word):
            logging.warning("[rcroc] rejected visual clue because it leaked answer word=%s", word)
            continue
        return clean[:1200]

    return None


def _image_prompt_from_clue(clue: str) -> str:
    return (
        "Это рисунок для игры в Крокодила. Изобрази только описанную сцену, без любого текста на изображении. "
        f"{IMAGE_STYLE}"
        f"Сцена: {clue}"
    )


async def _generate_word_image(word: str, chat_id: str) -> bytes | None:
    """GigaChat first; image providers never receive the literal secret answer."""
    from AI import picgeneration as pg
    from AI.gigachat_image import generate_gigachat_image

    visual_clue = await _build_visual_clue(word, chat_id)
    if not visual_clue:
        logging.warning("[rcroc] no safe visual clue generated word=%s", word)
        return None
    prompt_ru = _image_prompt_from_clue(visual_clue)

    # Даже после сборки prompt буквальная строка ответа не должна попасть ни в
    # GigaChat image, ни в резервные генераторы. Морфологические совпадения в
    # служебной фразе вроде «текста» для ответа «текст» здесь не считаем утечкой.
    if _prompt_contains_literal_secret(prompt_ru, word):
        logging.error("[rcroc] blocked unsafe image prompt that contains answer word=%s", word)
        return None

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


def _surrender_remaining_seconds(session: dict, *, now: float | None = None) -> int:
    """Return whole seconds left before surrender is allowed for this round."""
    started_at = session.get("started_at")
    if started_at is None:
        return 0
    current = time.monotonic() if now is None else now
    remaining = SURRENDER_DELAY_SECONDS - (current - float(started_at))
    if remaining <= 0:
        return 0
    return int(remaining + 0.999)


def _format_surrender_wait(seconds: int) -> str:
    minutes, rest = divmod(max(0, seconds), 60)
    return f"{minutes}:{rest:02d}"


async def start_game(message: types.Message, difficulty: str = "medium"):
    chat_id = str(message.chat.id)
    difficulty = normalize_difficulty(difficulty)
    word = pick_reverse_crocodile_word(difficulty)
    label = difficulty_label(difficulty)

    status = await message.answer(
        f"🦎 КРАКАДИЛ НАОБОРОТ\nСложность: {label}. Загадал слово, рисую свой шедевр..."
    )
    image = await _generate_word_image(word, chat_id)
    if not image:
        await status.edit_text("Не смог нарисовать, у меня лапки. Попробуй ещё раз.")
        return

    games[chat_id] = {
        "word": word,
        "difficulty": difficulty,
        "hints": 0,
        "image": image,
        # Пять минут считаются с готовности раунда, а не со старта AI-генерации.
        "started_at": time.monotonic(),
    }
    await status.delete()
    await bot.send_photo(
        chat_id=int(chat_id),
        photo=BufferedInputFile(image, "rcroc.png"),
        caption=(
            "🦎 <b>КРАКАДИЛ НАОБОРОТ</b>\n"
            f"Сложность: <b>{label}</b>\n"
            "Теперь рисую я, а вы угадываете. Пишите варианты в чат!"
        ),
        parse_mode="HTML",
        reply_markup=_keyboard(chat_id),
    )
    logging.info("[rcroc] start chat=%s difficulty=%s word=%s", chat_id, difficulty, word)


async def _finish_game(chat_id: str, text: str):
    session = games.pop(chat_id, None) or {}
    difficulty = normalize_difficulty(session.get("difficulty"))
    await bot.send_message(
        int(chat_id), text, parse_mode="HTML", reply_markup=_again_keyboard(difficulty)
    )
    await bot.send_message(
        int(chat_id),
        format_leaderboard(chat_id, "🏆 Самые умные педорасы"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def handle_callback(cb: types.CallbackQuery):
    data = cb.data or ""

    if data == "rcroc_choose_level":
        await cb.answer()
        await ask_difficulty(cb.message)
        return

    if data.startswith("rcroc_again_") or data.startswith("rcroc_level_"):
        difficulty = callback_difficulty(data)
        await cb.answer("Рисую новое...")
        await start_game(cb.message, difficulty)
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

    elif data.startswith("rcroc_stop_"):
        remaining = _surrender_remaining_seconds(session)
        if remaining:
            await cb.answer(
                f"Сдаться можно через {_format_surrender_wait(remaining)}.",
                show_alert=True,
            )
            return
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
