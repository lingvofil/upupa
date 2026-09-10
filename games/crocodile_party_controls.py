"""Controls, race guards, gallery paging and unified menu for Crocodile modes."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from types import SimpleNamespace

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from core.loader import bot
from features import crocodile_archive
from games import crocodile, crocodile_modes
from games import crocodile_party_state as party_state


GALLERY_PAGE_SIZE = 10

_configured = False
_original_handle_telephone_callback = None
_original_send_telephone_step = None
_original_finish_telephone = None
_original_record_drawing = None
_original_start_duel = None
_original_start_telephone = None


async def check_duel_answer_locked(message) -> bool:
    """Accept only the first correct duel answer, before any await can race."""
    chat_id = str(message.chat.id)
    duel = crocodile_modes.duel_games.get(chat_id)
    if not duel or duel.get("phase") != "drawing" or not message.text:
        return False
    artist_ids = {int(row[0]) for row in duel.get("artists", [])}
    if message.from_user and message.from_user.id in artist_ids:
        return False
    if not crocodile._contains_answer(message.text, duel.get("word", "")):
        return False

    # aiogram runs handlers cooperatively. With no await above this assignment,
    # the first correct answer closes the phase atomically for later updates.
    duel["phase"] = "answer_resolved"
    try:
        party_state.persist_party_modes(force=True)
    except Exception:
        logging.exception("[croc-duel] failed to persist resolved answer chat=%s", chat_id)

    if message.from_user:
        crocodile.add_point(chat_id, message.from_user.id, message.from_user.full_name)
    elapsed = max(0.0, time.time() - float(duel.get("started_at") or time.time()))
    try:
        from features.crocodile_scoring import record_artist_success

        for user_id, name in duel.get("artists", []):
            await record_artist_success(
                chat_id,
                user_id,
                name,
                elapsed_seconds=elapsed,
            )
    except Exception:
        logging.exception("[croc-duel] failed to record artist timing")

    winner = message.from_user.full_name if message.from_user else "Кто-то"
    try:
        await bot.send_message(
            int(chat_id),
            f"🎉 <b>{html.escape(winner)}</b> угадал: "
            f"<b>{html.escape(str(duel.get('word') or ''))}</b>. Теперь судим художников.",
            parse_mode="HTML",
        )
    except Exception:
        logging.exception("[croc-duel] failed to announce correct answer chat=%s", chat_id)
    await crocodile_modes._start_duel_vote(chat_id, duel)
    return True


def _telephone_controls_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Пропустить игрока",
                    callback_data=f"ctel_skip_{chat_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"ctel_cancel_{chat_id}",
                ),
            ]
        ]
    )


async def _send_telephone_step_with_controls(chat_id: str, game: dict) -> None:
    """Keep the original private-turn message and add resilient round controls."""
    await _original_send_telephone_step(chat_id, game)
    try:
        party_state.persist_party_modes(force=True)
    except Exception:
        logging.exception("[croc-party] failed to persist telephone step chat=%s", chat_id)
    if (
        crocodile_modes.telephone_games.get(str(chat_id)) is game
        and game.get("phase") == "playing"
        and int(game.get("step") or 0) < len(game.get("players", []))
    ):
        await bot.send_message(
            int(chat_id),
            "☎️ Если этот человек растворился в реальности — пропустите его. "
            "Телефон можно отменить и после старта.",
            reply_markup=_telephone_controls_keyboard(str(chat_id)),
        )


async def _record_drawing_without_phone_leak(
    chat_id,
    image,
    word,
    artists,
    mode="classic",
):
    """Hide telephone drawings from the gallery while their chain is active."""
    if str(mode) == "telephone" and str(chat_id) in crocodile_modes.telephone_games:
        return
    await _original_record_drawing(chat_id, image, word, artists, mode)


async def _finish_telephone_after_reveal(chat_id: str, game: dict) -> None:
    """Reveal the whole chain first, then publish its drawings to the gallery."""
    chain = list(game.get("chain", []))
    await _original_finish_telephone(chat_id, game)
    try:
        party_state.persist_party_modes(force=True)
    except Exception:
        logging.exception("[croc-party] failed to persist telephone finish chat=%s", chat_id)
    for item in chain:
        if item.get("kind") != "image":
            continue
        image = item.get("image")
        if not isinstance(image, (bytes, bytearray)) or not image:
            continue
        await _original_record_drawing(
            chat_id,
            bytes(image),
            str(item.get("value") or ""),
            [str(item.get("user_name") or "Игрок")],
            "telephone",
        )


async def _close_synthetic_room(chat_id: str, suffix: str) -> None:
    try:
        await crocodile.sio.close_room(crocodile_modes._chat_room(chat_id, suffix))
    except Exception:
        logging.exception(
            "[croc-party] failed to close canvas room chat=%s suffix=%s",
            chat_id,
            suffix,
        )


async def _cleanup_mode_canvases(chat_id: str, mode: str) -> None:
    parent_field = "duel_chat_id" if mode == "duel" else "telephone_chat_id"
    for key, session in list(crocodile_modes.canvas_sessions.items()):
        if str(session.get(parent_field) or "") != str(chat_id):
            continue
        crocodile_modes.canvas_sessions.pop(key, None)
        suffix = key.split(":", 1)[1] if ":" in key else ""
        if suffix:
            await _close_synthetic_room(str(chat_id), suffix)


async def _cancel_telephone(chat_id: str, game: dict, *, announce: bool = True) -> None:
    if crocodile_modes.telephone_games.get(chat_id) is not game:
        return
    crocodile_modes.telephone_games.pop(chat_id, None)
    try:
        party_state.persist_party_modes(force=True)
    except Exception:
        logging.exception("[croc-party] failed to persist telephone cancellation chat=%s", chat_id)
    await _cleanup_mode_canvases(chat_id, "telephone")
    if announce:
        await bot.send_message(
            int(chat_id),
            "☎️ Телефон положили обратно на рычаг. Цепочка отменена.",
        )


async def _cancel_duel(chat_id: str, duel: dict, *, announce: bool = True) -> None:
    if crocodile_modes.duel_games.get(chat_id) is not duel:
        return
    crocodile_modes.duel_games.pop(chat_id, None)
    try:
        party_state.persist_party_modes(force=True)
    except Exception:
        logging.exception("[croc-party] failed to persist duel cancellation chat=%s", chat_id)
    task = duel.get("vote_task")
    if task is not None and hasattr(task, "cancel"):
        task.cancel()
    await _cleanup_mode_canvases(chat_id, "duel")
    if announce:
        await bot.send_message(
            int(chat_id),
            "⚔️ Дуэль остановлена. Искусство снова вне опасности.",
        )


async def _skip_telephone(chat_id: str, game: dict) -> str:
    """Remove the current missing player but keep text/draw step parity."""
    if game.get("phase") != "playing":
        return "Цепочка ещё не идёт."
    step = int(game.get("step") or 0)
    players = game.get("players", [])
    if step >= len(players):
        return "Пропускать уже некого."

    _skipped_id, skipped_name = players.pop(step)
    key = crocodile_modes._session_key(chat_id, f"t{step}")
    crocodile_modes.canvas_sessions.pop(key, None)
    try:
        party_state.persist_party_modes(force=True)
    except Exception:
        logging.exception("[croc-party] failed to persist telephone skip chat=%s", chat_id)
    await _close_synthetic_room(chat_id, f"t{step}")
    await bot.send_message(
        int(chat_id),
        f"⏭ <b>{html.escape(str(skipped_name))}</b> пропущен. "
        "Его роль в цепочке подхватывает следующий игрок.",
        parse_mode="HTML",
    )

    # Do not increment: the replacement player must perform the same role
    # (text or drawing) so the chain keeps alternating correctly.
    await crocodile_modes._send_telephone_step(chat_id, game)
    return f"Пропущен: {skipped_name}"


def _telephone_participants(game: dict) -> set[int]:
    participants = {int(user_id) for user_id, _name in game.get("players", [])}
    host_id = int(game.get("host_id") or 0)
    if host_id > 0:
        participants.add(host_id)
    return participants


async def handle_telephone_callback_resilient(callback) -> None:
    data = callback.data or ""
    if data.startswith("ctel_skip_"):
        chat_id = data[len("ctel_skip_"):]
        game = crocodile_modes.telephone_games.get(chat_id)
        if not game or game.get("phase") != "playing":
            return await callback.answer("Эта цепочка уже закрыта")
        if callback.from_user.id not in _telephone_participants(game):
            return await callback.answer(
                "Пропускать могут только участники цепочки",
                show_alert=True,
            )
        await callback.answer("Пропускаем")
        await _skip_telephone(chat_id, game)
        return

    if data.startswith("ctel_cancel_"):
        chat_id = data[len("ctel_cancel_"):]
        game = crocodile_modes.telephone_games.get(chat_id)
        if not game:
            return await callback.answer("Эта цепочка уже закрыта")
        if callback.from_user.id != int(game.get("host_id") or 0):
            return await callback.answer(
                "Отменить кнопкой может только ведущий. "
                "Участники могут написать «кракадил стоп».",
                show_alert=True,
            )
        await callback.answer("Отменено")
        await _cancel_telephone(chat_id, game)
        return

    await _original_handle_telephone_callback(callback)


def _reverse_active(chat_id: str) -> bool:
    try:
        from games import reverse_crocodile

        return chat_id in reverse_crocodile.games
    except Exception:
        return False


def has_active_party(chat_id: int | str) -> bool:
    chat_id = str(chat_id)
    return bool(
        chat_id in crocodile.game_sessions
        or chat_id in crocodile_modes.duel_games
        or chat_id in crocodile_modes.telephone_games
        or _reverse_active(chat_id)
    )


def has_active_non_reverse_party(chat_id: int | str) -> bool:
    chat_id = str(chat_id)
    return bool(
        chat_id in crocodile.game_sessions
        or chat_id in crocodile_modes.duel_games
        or chat_id in crocodile_modes.telephone_games
    )


def party_status_text(chat_id: int | str) -> str:
    chat_id = str(chat_id)
    telephone = crocodile_modes.telephone_games.get(chat_id)
    if telephone:
        if telephone.get("phase") == "lobby":
            return (
                "☎️ Сейчас собирается испорченный телефон: "
                f"участников {len(telephone.get('players', []))}."
            )
        step = int(telephone.get("step") or 0)
        players = telephone.get("players", [])
        current = str(players[step][1]) if step < len(players) else "финал"
        return (
            "☎️ Сейчас идёт испорченный телефон: "
            f"ход {step + 1}, сейчас — {current}."
        )

    duel = crocodile_modes.duel_games.get(chat_id)
    if duel:
        phase_labels = {
            "lobby": "ищем второго художника",
            "drawing": "оба рисуют",
            "answer_resolved": "ответ уже принят, готовим голосование",
            "voting": "идёт голосование",
        }
        artists = " + ".join(
            str(name) for _user_id, name in duel.get("artists", [])
        ) or "художники"
        phase = phase_labels.get(str(duel.get("phase")), "активна")
        return f"⚔️ Сейчас идёт дуэль: {artists}; {phase}."

    regular = crocodile.game_sessions.get(chat_id)
    if regular:
        names = crocodile_modes.session_artist_names(regular)
        if str(regular.get("mode") or "") == "duo" or len(names) > 1:
            return f"👥 Сейчас идёт совместный кракадил: {' + '.join(names)} рисуют вдвоём."
        return (
            "🎨 Сейчас идёт обычный кракадил: "
            f"рисует {regular.get('drawer_name', 'художник')}."
        )

    if _reverse_active(chat_id):
        return "🦎 Сейчас идёт кракадил наоборот: Упупа рисует, чат страдает и угадывает."
    return (
        "🦎 Сейчас ничего не идёт. Выбирай, каким способом унижать "
        "изобразительное искусство."
    )


def menu_keyboard(chat_id: int | str) -> InlineKeyboardMarkup:
    chat_id = str(chat_id)
    if has_active_party(chat_id):
        rows: list[list[InlineKeyboardButton]] = []
        telephone = crocodile_modes.telephone_games.get(chat_id)
        if telephone and telephone.get("phase") == "playing":
            rows.append(
                [InlineKeyboardButton(text="⏭ Пропустить игрока", callback_data="cmenu_skip")]
            )
        rows.append(
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="cmenu_refresh")]
        )
        if not _reverse_active(chat_id):
            rows.append(
                [InlineKeyboardButton(text="🛑 Остановить", callback_data="cmenu_stop")]
            )
        rows.append([InlineKeyboardButton(text="🖼 Галерея", callback_data="cmenu_gallery")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Обычный / вдвоём", callback_data="cmenu_classic")],
            [
                InlineKeyboardButton(text="⚔️ Дуэль", callback_data="cmenu_duel"),
                InlineKeyboardButton(text="☎️ Телефон", callback_data="cmenu_phone"),
            ],
            [InlineKeyboardButton(text="🦎 Наоборот", callback_data="cmenu_reverse")],
            [InlineKeyboardButton(text="🖼 Галерея", callback_data="cmenu_gallery")],
        ]
    )


async def show_menu(message) -> None:
    await message.answer(
        party_status_text(message.chat.id),
        reply_markup=menu_keyboard(message.chat.id),
    )


def _callback_message_proxy(callback) -> SimpleNamespace:
    return SimpleNamespace(
        chat=callback.message.chat,
        from_user=callback.from_user,
        answer=callback.message.answer,
        reply=callback.message.answer,
    )


async def _stop_active_party(chat_id: str, user_id: int) -> tuple[bool, str]:
    telephone = crocodile_modes.telephone_games.get(chat_id)
    if telephone:
        if user_id not in _telephone_participants(telephone):
            return False, "Остановить телефон может только его участник."
        await _cancel_telephone(chat_id, telephone)
        return True, "Телефон остановлен."

    duel = crocodile_modes.duel_games.get(chat_id)
    if duel:
        participants = {int(user_id) for user_id, _name in duel.get("artists", [])}
        host_id = int(duel.get("host_id") or 0)
        if host_id > 0:
            participants.add(host_id)
        if user_id not in participants:
            return False, "Остановить дуэль может только один из её участников."
        await _cancel_duel(chat_id, duel)
        return True, "Дуэль остановлена."

    session = crocodile.game_sessions.get(chat_id)
    if session:
        from games.crocodile_controls import stop_lock_message

        lock_message = stop_lock_message(session, user_id)
        if lock_message:
            return False, lock_message
        await crocodile._stop_session(chat_id, reason="unified stop")
        return True, "🛑 Игра остановлена."

    if _reverse_active(chat_id):
        return False, (
            "Для кракадила наоборот используй кнопку «🏳️ Сдаёмся» "
            "в карточке раунда."
        )
    return False, "Игра не запущена."


async def stop_crocodile(message) -> None:
    user_id = int(message.from_user.id) if message.from_user else 0
    stopped, text = await _stop_active_party(str(message.chat.id), user_id)
    # Telephone/duel cancellation already posts an explicit chat announcement.
    if not stopped or text.startswith("🛑"):
        await message.reply(text)


async def handle_menu_callback(callback) -> None:
    data = callback.data or ""
    chat_id = str(callback.message.chat.id)
    if data == "cmenu_refresh":
        await callback.answer()
        await callback.message.edit_text(
            party_status_text(chat_id),
            reply_markup=menu_keyboard(chat_id),
        )
        return
    if data == "cmenu_gallery":
        await callback.answer("Открываю галерею")
        await send_gallery_page(callback.message, page=0)
        return
    if data == "cmenu_skip":
        game = crocodile_modes.telephone_games.get(chat_id)
        if not game or game.get("phase") != "playing":
            return await callback.answer("Телефон уже закончился", show_alert=True)
        if callback.from_user.id not in _telephone_participants(game):
            return await callback.answer(
                "Пропускать могут только участники",
                show_alert=True,
            )
        await callback.answer("Пропускаем")
        await _skip_telephone(chat_id, game)
        return
    if data == "cmenu_stop":
        stopped, text = await _stop_active_party(chat_id, int(callback.from_user.id))
        await callback.answer(text, show_alert=not stopped)
        if stopped and text.startswith("🛑"):
            await callback.message.answer(text)
        return

    if has_active_party(chat_id):
        return await callback.answer(
            "Сначала закончи текущую партию",
            show_alert=True,
        )

    proxy = _callback_message_proxy(callback)
    if data == "cmenu_classic":
        await callback.answer("Готовим холст")
        await crocodile.start_new_game(
            callback.message.chat.id,
            callback.from_user.id,
            callback.from_user.full_name,
        )
        return
    if data == "cmenu_duel":
        await callback.answer("Вызываем жертву")
        await crocodile_modes.start_duel(proxy)
        return
    if data == "cmenu_phone":
        await callback.answer("Собираем цепочку")
        await crocodile_modes.start_telephone(proxy)
        return
    if data == "cmenu_reverse":
        await callback.answer("Выбираем режим")
        from games import reverse_crocodile_modes

        await reverse_crocodile_modes.ask_mode(proxy)
        return


def _gallery_rows_for_page(chat_id: int | str, page: int) -> tuple[list[dict], int]:
    rows = [
        row
        for row in crocodile_archive._load()
        if str(row.get("chat_id")) == str(chat_id)
    ]
    rows.reverse()
    page = max(0, int(page))
    start = page * GALLERY_PAGE_SIZE
    selected = rows[start : start + GALLERY_PAGE_SIZE]
    selected.reverse()
    return selected, len(rows)


def _gallery_nav_keyboard(page: int, total: int) -> InlineKeyboardMarkup | None:
    page = max(0, int(page))
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(text="⬅️ Новее", callback_data=f"cgal_page_{page - 1}")
        )
    if (page + 1) * GALLERY_PAGE_SIZE < total:
        buttons.append(
            InlineKeyboardButton(text="Старее ➡️", callback_data=f"cgal_page_{page + 1}")
        )
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


async def send_gallery_page(message, page: int = 0) -> None:
    rows, total = await asyncio.to_thread(_gallery_rows_for_page, message.chat.id, page)
    valid: list[tuple[dict, bytes]] = []
    for row in rows:
        try:
            image = await asyncio.to_thread(
                (crocodile_archive.GALLERY_DIR / row["file"]).read_bytes
            )
        except (OSError, KeyError):
            continue
        valid.append((row, image))

    if not valid:
        if total and page > 0:
            await message.answer(
                "🖼 На этой странице файлы уже протухли. Вернись к более новым."
            )
        else:
            await message.answer(
                "🖼 Галерея пока пустая. Сначала хоть что-нибудь нарисуйте."
            )
        return

    media = []
    mode_labels = {
        "classic": "обычный",
        "duo": "вдвоём",
        "duel": "дуэль",
        "telephone": "телефон",
    }
    for row, image in valid:
        artists = " + ".join(row.get("artists") or ["неизвестный хуйдожник"])
        word = row.get("word") or "?"
        raw_mode = str(row.get("mode") or "classic")
        mode = mode_labels.get(raw_mode, raw_mode)
        media.append(
            InputMediaPhoto(
                media=BufferedInputFile(image, filename="crocodile.jpg"),
                caption=f"🎨 {artists}\nСлово: {word}\nРежим: {mode}",
            )
        )
    await message.answer_media_group(media)
    pages = max(1, (total + GALLERY_PAGE_SIZE - 1) // GALLERY_PAGE_SIZE)
    await message.answer(
        f"🖼 Галерея: страница {min(page + 1, pages)}/{pages}",
        reply_markup=_gallery_nav_keyboard(page, total),
    )


async def handle_gallery_callback(callback) -> None:
    data = callback.data or ""
    if not data.startswith("cgal_page_"):
        return
    try:
        page = max(0, int(data[len("cgal_page_"):]))
    except ValueError:
        return await callback.answer("Кривая страница", show_alert=True)
    await callback.answer()
    await send_gallery_page(callback.message, page=page)


async def _start_duel_guarded(message) -> None:
    chat_id = str(message.chat.id)
    if _reverse_active(chat_id):
        await message.answer("Сначала закончите текущего кракадила наоборот.")
        return
    await _original_start_duel(message)


async def _start_telephone_guarded(message) -> None:
    chat_id = str(message.chat.id)
    if _reverse_active(chat_id):
        await message.answer("Сначала закончите текущего кракадила наоборот.")
        return
    await _original_start_telephone(message)


def install_crocodile_help() -> None:
    """Add party-mode commands to the existing interactive help section."""
    try:
        from prompts import help_texts

        section = help_texts.HELP_DICT.get("creative", "")
        if "<code>кракадил дуэль</code>" in section:
            return
        old = "<code>кракадил</code> - норисуй даунский рисунок\n"
        new = (
            "<code>кракадил</code> - единое меню и статус текущей партии\n"
            "<code>кракадил дуэль</code> - два художника рисуют одно слово параллельно\n"
            "<code>кракадил телефон</code> - испорченный телефон: слово → рисунок → догадка\n"
            "<code>кракадил галерея</code> - рисунки чата с просмотром старых страниц\n"
        )
        if old in section:
            section = section.replace(old, new, 1)
        else:
            section += "\n" + new
        help_texts.HELP_DICT["creative"] = section
        help_texts.HELP_TEXT = "\n\n".join(help_texts.HELP_DICT.values())
        try:
            import prompts

            prompts.HELP_TEXT = help_texts.HELP_TEXT
        except Exception:
            pass
    except Exception:
        logging.exception("[croc-party] failed to extend help text")


def configure_crocodile_party_controls() -> None:
    """Install party-mode controls after ``configure_crocodile_modes``."""
    global _configured
    global _original_handle_telephone_callback, _original_send_telephone_step
    global _original_finish_telephone, _original_record_drawing
    global _original_start_duel, _original_start_telephone
    if _configured:
        return

    _original_handle_telephone_callback = crocodile_modes.handle_telephone_callback
    _original_send_telephone_step = crocodile_modes._send_telephone_step
    _original_finish_telephone = crocodile_modes._finish_telephone
    _original_record_drawing = crocodile_modes.record_drawing
    _original_start_duel = crocodile_modes.start_duel
    _original_start_telephone = crocodile_modes.start_telephone

    crocodile_modes.check_duel_answer = check_duel_answer_locked
    crocodile_modes.handle_telephone_callback = handle_telephone_callback_resilient
    crocodile_modes._send_telephone_step = _send_telephone_step_with_controls
    crocodile_modes.record_drawing = _record_drawing_without_phone_leak
    crocodile_modes._finish_telephone = _finish_telephone_after_reveal
    crocodile_modes.start_duel = _start_duel_guarded
    crocodile_modes.start_telephone = _start_telephone_guarded
    install_crocodile_help()
    _configured = True
