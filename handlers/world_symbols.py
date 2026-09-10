"""Generation and management of official state symbols in World of Upupa."""

from __future__ import annotations

import asyncio
import logging
import re

from aiogram import F, Router, types
from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from AI.gigachat_image import generate_gigachat_image
from core.loader import bot
from features.world.identity import ensure_state_identity
from features.world.permissions import is_strict_chat_admin
from features.world.service import get_world_service
from features.world.symbols import (
    WorldSymbolStore,
    build_state_symbol_prompt,
    symbol_kind_label,
)
from handlers.world import _require_world, _title


router = Router(name="world_symbols")
_SYMBOL_COMMAND_RE = re.compile(
    r"^\s*упупа[\s,.:;!-]+(флаг|герб)\b(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_SHOW_COMMAND_RE = re.compile(
    r"^\s*упупа[\s,.:;!-]+(?:символ|символика)\s*$",
    re.IGNORECASE,
)


def _parse_symbol_command(text: str | None) -> tuple[str, str | None] | None:
    match = _SYMBOL_COMMAND_RE.match(text or "")
    if not match:
        return None
    kind = "flag" if match.group(1).lower() == "флаг" else "emblem"
    idea = " ".join((match.group(2) or "").split()).strip() or None
    return kind, idea


def _is_show_command(message: types.Message) -> bool:
    return bool(message.text and _SHOW_COMMAND_RE.match(message.text))


def _store_for_service(service) -> WorldSymbolStore | None:
    ledger = getattr(service, "ledger", None)
    path = getattr(ledger, "path", None)
    return WorldSymbolStore(path) if path is not None else None


def _symbol_menu_markup(*, has_symbol: bool) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏳 Сгенерировать флаг", callback_data="worldsymbol:flag")
    builder.button(text="🛡 Сгенерировать герб", callback_data="worldsymbol:emblem")
    if has_symbol:
        builder.button(text="👁 Показать текущий", callback_data="worldsymbol:show")
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    builder.adjust(1)
    return builder.as_markup()


async def _symbol_menu_text(chat_id: int, title: str) -> tuple[str, types.InlineKeyboardMarkup] | None:
    service = get_world_service()
    state = await service.get_state(chat_id, title)
    if state is None or not state.enabled:
        return None
    store = _store_for_service(service)
    symbol = await asyncio.to_thread(store.get_symbol, state.world_id) if store else None
    current = (
        f"Сейчас официальный символ — {symbol_kind_label(symbol.kind)}."
        if symbol
        else "Официального флага или герба пока нет."
    )
    text = (
        "🏴 Государственная символика\n\n"
        f"{current}\n\n"
        "Кнопка сама придумает символ по характеру государства. "
        "Если хочешь подкинуть конкретную дурь, напиши:\n"
        "<code>упупа флаг пьяный опоссум и три молнии</code>\n"
        "или <code>упупа герб кот на офисном кресле</code>.\n\n"
        "Новый результат сразу становится официальным. Менять символ могут только администраторы чата."
    )
    return text, _symbol_menu_markup(has_symbol=bool(symbol))


async def _generate_and_install(
    message: types.Message,
    *,
    requester_id: int,
    kind: str,
    idea: str | None,
) -> bool:
    service = await _require_world(message)
    if service is None:
        return False
    if not await is_strict_chat_admin(bot, message.chat.id, requester_id):
        await message.reply("🏴 Государственную символику могут менять только администраторы государства.")
        return False

    state = await service.get_state(message.chat.id, _title(message))
    if state is None:
        return False
    store = _store_for_service(service)
    if store is None:
        await message.reply("🏴 Министерство символики потеряло архив. Попробуй позже.")
        return False

    identity = await ensure_state_identity(service, state)
    details = identity.details if identity is not None else await service.get_details(state.world_id)
    prompt = build_state_symbol_prompt(state, details, kind, idea=idea)
    label = symbol_kind_label(kind)

    progress = await message.reply(f"🎨 Рисую государственный {label}. Геральдисты уже ругаются.")
    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
        image = await generate_gigachat_image(prompt)
        if not image:
            await progress.edit_text("🎨 Художник съел техническое задание. Символ не получился, попробуй ещё раз.")
            return False

        sent = await message.reply_photo(
            BufferedInputFile(image, filename=f"upupa_{kind}_{state.world_id}.png"),
            caption=(
                f"{'🏳' if kind == 'flag' else '🛡'} Государственный {label} "
                f"№{state.world_id} «{state.title}» утверждён."
            ),
        )
        if not sent.photo:
            await progress.edit_text("🏴 Картинку отправил, но Telegram не дал сохранить её как государственный символ.")
            return False
        file_id = sent.photo[-1].file_id
        try:
            await asyncio.to_thread(store.set_symbol, state.world_id, kind, file_id, prompt)
        except Exception:
            logging.exception("Failed to persist World symbol state=%s kind=%s", state.world_id, kind)
            try:
                await sent.delete()
            except Exception:
                pass
            await progress.edit_text("🏴 Нарисовал, но архивариус уронил печать. Символ не утверждён.")
            return False

        try:
            await progress.delete()
        except Exception:
            pass
        return True
    except Exception:
        logging.exception("World symbol generation failed state=%s kind=%s", state.world_id, kind)
        try:
            await progress.edit_text("🎨 Художник взорвался. Символ не изменён.")
        except Exception:
            pass
        return False


@router.message(lambda message: _parse_symbol_command(message.text) is not None)
async def generate_state_symbol_command(message: types.Message):
    parsed = _parse_symbol_command(message.text)
    if parsed is None or message.from_user is None:
        return
    kind, idea = parsed
    await _generate_and_install(
        message,
        requester_id=message.from_user.id,
        kind=kind,
        idea=idea,
    )


@router.message(_is_show_command)
async def show_state_symbol_command(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    state = await service.get_state(message.chat.id, _title(message))
    if state is None:
        return
    store = _store_for_service(service)
    symbol = await asyncio.to_thread(store.get_symbol, state.world_id) if store else None
    if symbol is None:
        await message.reply("🏴 У государства пока нет официального символа. Открой «упупа миры» и нарисуй его.")
        return
    await message.reply_photo(
        symbol.telegram_file_id,
        caption=(
            f"{'🏳' if symbol.kind == 'flag' else '🛡'} Официальный {symbol_kind_label(symbol.kind)} "
            f"государства №{state.world_id} «{state.title}»."
        ),
    )


@router.callback_query(F.data.startswith("worldsymbol:"))
async def world_symbol_callback(query: types.CallbackQuery):
    if query.message is None or query.from_user is None:
        return

    action = (query.data or "").split(":", 1)[-1]
    service = get_world_service()
    state = await service.get_state(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
    )
    if state is None or not state.enabled:
        await query.answer("Этот чат сейчас вне Мира Упупы.", show_alert=True)
        return

    if action == "menu":
        payload = await _symbol_menu_text(query.message.chat.id, state.title)
        if payload is None:
            await query.answer("Символика недоступна.", show_alert=True)
            return
        text, markup = payload
        await query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await query.answer()
        return

    if action == "show":
        store = _store_for_service(service)
        symbol = await asyncio.to_thread(store.get_symbol, state.world_id) if store else None
        if symbol is None:
            await query.answer("Официального символа пока нет.", show_alert=True)
            return
        await query.answer()
        await query.message.answer_photo(
            symbol.telegram_file_id,
            caption=(
                f"{'🏳' if symbol.kind == 'flag' else '🛡'} Официальный {symbol_kind_label(symbol.kind)} "
                f"государства №{state.world_id} «{state.title}»."
            ),
        )
        return

    if action not in {"flag", "emblem"}:
        await query.answer("Неизвестный символ.", show_alert=True)
        return
    if not await is_strict_chat_admin(bot, query.message.chat.id, query.from_user.id):
        await query.answer("Менять государственный символ могут только администраторы.", show_alert=True)
        return

    await query.answer("Геральдисты приступили")
    await _generate_and_install(
        query.message,
        requester_id=query.from_user.id,
        kind=action,
        idea=None,
    )
