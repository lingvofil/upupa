"""Хэндлеры: Егра, мемы, кракадил."""
from aiogram import Router

from aiogram import Bot, F, types
from aiogram.types import Message, PollAnswer
from core.loader import bot
from features.crocodile_scoring import (
    format_artist_leaderboard,
    format_slowest_artist_leaderboard,
)
from games.egra import start_egra, handle_egra_answer, handle_final_button_press
from services import memegenerator
from games import (
    crocodile,
    crocodile_likes,
    crocodile_modes,
    crocodile_party_controls,
    reverse_crocodile,
    reverse_crocodile_modes,
)
from AI.quiz import process_poll_answer


router = Router(name="games")
_quiz_poll_answers_in_progress: set[str] = set()
_reverse_croc_starts_in_progress: set[int] = set()


async def _process_quiz_poll_answer_once(poll_answer: PollAnswer, bot: Bot) -> bool:
    """Не даёт параллельным ответам на один poll несколько раз двинуть викторину."""
    poll_id = poll_answer.poll_id
    if poll_id in _quiz_poll_answers_in_progress:
        return False
    _quiz_poll_answers_in_progress.add(poll_id)
    try:
        await process_poll_answer(poll_answer, bot)
        return True
    finally:
        _quiz_poll_answers_in_progress.discard(poll_id)


def _claim_reverse_croc_start(chat_id: int) -> bool:
    """Атомарно резервирует запуск reverse Crocodile до первого await."""
    if chat_id in _reverse_croc_starts_in_progress:
        return False
    if str(chat_id) in reverse_crocodile.games:
        return False
    _reverse_croc_starts_in_progress.add(chat_id)
    return True


def _release_reverse_croc_start(chat_id: int) -> None:
    _reverse_croc_starts_in_progress.discard(chat_id)


def _is_new_reverse_start(data: str) -> bool:
    if data.startswith("rcrocm_again_"):
        return True
    return data in {
        "rcrocm_reveal", "rcrocm_movie", "rcrocm_cartoon",
        "rcrocm_proverb", "rcrocm_saying",
    }


@router.message(F.text.lower() == "егра")
async def egra_command_handler(message: types.Message):
    await start_egra(message, bot)


@router.poll_answer()
async def handle_poll_answers(poll_answer: PollAnswer, bot: Bot):
    is_egra_handled = await handle_egra_answer(poll_answer, bot)
    if not is_egra_handled:
        await _process_quiz_poll_answer_once(poll_answer, bot)


@router.callback_query(F.data == "egra_final_choice")
async def egra_callback_handler(callback_query: types.CallbackQuery):
    await handle_final_button_press(callback_query, bot)


@router.message(F.text.lower().in_(["мем", "meme"]))
async def meme_command_handler(message: Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    reply_text = message.reply_to_message.text if message.reply_to_message else None
    photo = await memegenerator.create_meme_image(message.chat.id, reply_text)
    if photo:
        await message.answer_photo(photo)
    else:
        await message.answer("Ошибка при создании мема.")


@router.message(F.text.lower() == "кракадил")
async def start_croc(message: types.Message):
    await crocodile_party_controls.show_menu(message)


@router.message(F.text.lower() == "кракадил дуэль")
async def start_croc_duel(message: types.Message):
    await crocodile_modes.start_duel(message)


@router.message(
    lambda m: m.text and m.text.lower().strip() in {
        "кракадил телефон", "кракадил испорченный телефон"
    }
)
async def start_croc_telephone(message: types.Message):
    await crocodile_modes.start_telephone(message)


@router.message(F.text.lower() == "кракадил галерея")
async def croc_gallery(message: types.Message):
    await crocodile_party_controls.send_gallery_page(message)


@router.message(
    lambda m: m.text and m.text.lower().strip() in {
        "кракадил художники", "кракадил хуйдожники"
    }
)
async def croc_artist_stats(message: types.Message):
    await message.answer(
        format_artist_leaderboard(message.chat.id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(
    lambda m: m.text and m.text.lower().strip() in {
        "кракадил художники время", "кракадил хуйдожники время", "кракадил долго"
    }
)
async def croc_slowest_artist_stats(message: types.Message):
    await message.answer(
        format_slowest_artist_leaderboard(message.chat.id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(
    F.data.startswith("cr_") | F.data.in_(("btn_like", "btn_want_draw"))
)
async def croc_callback(callback: types.CallbackQuery):
    if callback.data == "btn_like":
        await crocodile_likes.handle_like_callback(callback)
    elif callback.data == "cr_restart":
        await crocodile.handle_start_game(callback.message)
        await callback.answer()
    else:
        await crocodile.handle_callback(callback)


@router.callback_query(F.data.startswith("cduel_"))
async def croc_duel_callback(callback: types.CallbackQuery):
    await crocodile_modes.handle_duel_callback(callback)


@router.callback_query(F.data.startswith("ctel_"))
async def croc_telephone_callback(callback: types.CallbackQuery):
    await crocodile_modes.handle_telephone_callback(callback)


@router.callback_query(F.data.startswith("cmenu_"))
async def croc_menu_callback(callback: types.CallbackQuery):
    await crocodile_party_controls.handle_menu_callback(callback)


@router.callback_query(F.data.startswith("cgal_"))
async def croc_gallery_callback(callback: types.CallbackQuery):
    await crocodile_party_controls.handle_gallery_callback(callback)


@router.message(lambda m: m.text and m.text.lower().strip() == "кракадил стоп")
async def stop_croc_text(message: types.Message):
    await crocodile_party_controls.stop_crocodile(message)


@router.message(F.text.lower() == "кракадил наоборот")
async def start_reverse_croc(message: types.Message):
    chat_id = message.chat.id
    if crocodile_party_controls.has_active_non_reverse_party(chat_id):
        await message.answer(
            crocodile_party_controls.party_status_text(chat_id)
            + "\nСначала закончи эту партию."
        )
        return
    if chat_id in _reverse_croc_starts_in_progress or str(chat_id) in reverse_crocodile.games:
        await message.answer("🦎 Раунд уже запускается или идёт.")
        return
    await reverse_crocodile_modes.ask_mode(message)


@router.callback_query(F.data.startswith("rcroc_"))
async def reverse_croc_callback(callback: types.CallbackQuery):
    data = callback.data or ""

    if data == "rcroc_choose_level":
        await reverse_crocodile.handle_callback(callback)
        return

    if data.startswith("rcroc_again_") or data.startswith("rcroc_level_"):
        chat_id = callback.message.chat.id
        if not _claim_reverse_croc_start(chat_id):
            await callback.answer(
                "Новый раунд уже запускается или идёт.",
                show_alert=True,
            )
            return
        difficulty = reverse_crocodile.callback_difficulty(data)
        await callback.answer("Рисую новое...")
        try:
            await reverse_crocodile.start_game(callback.message, difficulty)
        finally:
            _release_reverse_croc_start(chat_id)
        return

    await reverse_crocodile.handle_callback(callback)


@router.callback_query(F.data.startswith("rcrocm_"))
async def reverse_croc_mode_callback(callback: types.CallbackQuery):
    data = callback.data or ""
    if not _is_new_reverse_start(data):
        await reverse_crocodile_modes.handle_callback(callback)
        return

    chat_id = callback.message.chat.id
    if not _claim_reverse_croc_start(chat_id):
        await callback.answer(
            "Новый раунд уже запускается или идёт.",
            show_alert=True,
        )
        return
    try:
        await reverse_crocodile_modes.handle_callback(callback)
    finally:
        _release_reverse_croc_start(chat_id)
