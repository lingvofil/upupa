"""Readable entry points over the social graph: dossier, roles, factions and buttons.

Commands:
    «досье» / «досье @user» / «досье» реплаем — карточка человека
    «роли»     — роли участников чата
    «фракции»  — лагеря чата с раскрашенным графом

All ``sg:`` inline buttons (map, relationship card, dossier) are dispatched here.
"""

from __future__ import annotations

import logging
import re
import time

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings import BLOCKED_USERS
from features.social_graph.analysis import select_render_graph
from features.social_graph.digest import build_digest_facts, digest_keyboard, write_digest_html
from features.social_graph.cards import fetch_avatar, render_dossier_card_async
from features.social_graph.overview import (
    build_dossier,
    dossier_card_data,
    faction_colors,
    faction_legend,
    faction_names,
    format_dossier_caption_html,
    format_dossier_html,
    format_factions_html,
    format_roles_html,
    load_structure,
)
from features.social_graph.rendering import render_graph_png_async
from features.social_graph.service import get_graph_data, is_social_graph_enabled, resolve_relationship_usernames
from handlers import social_graph as graph_handlers
from handlers.chronicle import MODE_RECENT, send_chronicle_page
from infrastructure.ai.execution import ai_feature


router = Router(name="social_navigation")
CALLBACK_PREFIX = graph_handlers.CALLBACK_PREFIX
CRINGE_BUTTON_COOLDOWN_SECONDS = 180
_DOSSIER_RE = re.compile(r"^досье(?:\s+@([A-Za-z0-9_]{3,}))?$", re.IGNORECASE)
_cringe_button_used_at: dict[int, float] = {}


def _allowed_user(user) -> bool:
    return bool(user) and not getattr(user, "is_bot", False) and user.id not in BLOCKED_USERS


def _normalized(message: types.Message) -> str:
    return " ".join((message.text or "").strip().split())


def _is_dossier_command(message: types.Message) -> bool:
    return _allowed_user(message.from_user) and bool(_DOSSIER_RE.fullmatch(_normalized(message)))


def _is_exact(message: types.Message, command: str) -> bool:
    return _allowed_user(message.from_user) and _normalized(message).casefold() == command


def _is_group_chat(chat) -> bool:
    return getattr(chat.type, "value", chat.type) in {"group", "supergroup"}


def dossier_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="❤️ Все отношения", callback_data=f"{CALLBACK_PREFIX}rels:{int(user_id)}")
    builder.button(text="📜 Летопись", callback_data=f"{CALLBACK_PREFIX}uchr:{int(user_id)}")
    builder.adjust(2)
    return builder.as_markup()


async def _resolve_dossier_target(message: types.Message) -> tuple[int, str] | None:
    match = _DOSSIER_RE.fullmatch(_normalized(message))
    username = match.group(1) if match else None
    if username:
        resolved = await resolve_relationship_usernames(message.chat.id, [username])
        person = resolved.get(username.lower())
        if person is None:
            return None
        return int(person[0]), str(person[1])
    replied = getattr(message, "reply_to_message", None)
    target = getattr(replied, "from_user", None) if replied else None
    if target is not None and not target.is_bot:
        return int(target.id), target.full_name or target.first_name or "Участник"
    user = message.from_user
    return int(user.id), user.full_name or user.first_name or "Участник"


async def send_dossier(message: types.Message, chat_id: int, user_id: int, fallback_name: str) -> None:
    dossier = await build_dossier(chat_id, user_id, fallback_name)
    link = None
    if dossier.latest_event is not None:
        event = dossier.latest_event
        message_id = event.anchor_message_id or (event.source_message_ids[0] if event.source_message_ids else None)
        link = graph_handlers._telegram_message_link(chat_id, message_id)
    bot = getattr(message, "bot", None)
    if bot is not None and hasattr(message, "reply_photo"):
        try:
            avatar = await fetch_avatar(bot, user_id)
            png = await render_dossier_card_async(dossier_card_data(dossier, avatar))
            await message.reply_photo(
                BufferedInputFile(png, filename="dossier.png"),
                caption=format_dossier_caption_html(dossier, link),
                parse_mode="HTML",
                reply_markup=dossier_keyboard(user_id),
            )
            return
        except Exception as exc:
            logging.warning("[social_graph] dossier card failed, falling back to text: %s", exc, exc_info=True)
    await message.reply(
        format_dossier_html(dossier, link),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=dossier_keyboard(user_id),
    )


async def send_roles(message: types.Message, chat_id: int) -> None:
    structure = await load_structure(chat_id)
    text = format_roles_html(structure)
    if not text:
        await message.reply(f"За последние {structure.period_days} дней ролей не разглядеть: связей почти нет.")
        return
    await message.reply(text, parse_mode="HTML")


@ai_feature("фракции", only_if_unset=True)
async def send_factions(message: types.Message, chat_id: int) -> None:
    structure = await load_structure(chat_id)
    if not structure.factions.factions:
        await message.reply(f"За последние {structure.period_days} дней фракций не видно: связей слишком мало.")
        return
    names = await faction_names(structure)
    view = select_render_graph(structure.edges, structure.names)
    if view.edges:
        try:
            png = await render_graph_png_async(
                view,
                node_colors=faction_colors(structure),
                legend=faction_legend(structure, names),
            )
            await message.answer_photo(BufferedInputFile(png, filename="social_factions.png"))
        except Exception as exc:
            logging.warning("[social_graph] faction render failed: %s", exc, exc_info=True)
    await message.reply(format_factions_html(structure, names), parse_mode="HTML")


async def _ensure(message: types.Message) -> bool:
    if not _is_group_chat(message.chat):
        await message.reply("Соцграф работает только внутри группового чата.")
        return False
    if not is_social_graph_enabled(message.chat.id):
        await message.reply(graph_handlers._disabled_text())
        return False
    return True


@router.message(_is_dossier_command)
async def handle_dossier(message: types.Message):
    if not await _ensure(message):
        return
    target = await _resolve_dossier_target(message)
    if target is None:
        await message.reply("Не знаю такого гражданина. Напиши «досье @user» про того, кто уже писал в чат, или ответь «досье» на его сообщение.")
        return
    await send_dossier(message, message.chat.id, *target)


@router.message(lambda message: _is_exact(message, "светская хроника"))
async def handle_social_digest(message: types.Message):
    if not await _ensure(message):
        return
    facts = await build_digest_facts(message.chat.id)
    if not facts.is_meaningful:
        await message.reply("🥂 За неделю светской жизни не случилось: ни заметных пар, ни событий в Летописи.")
        return
    await message.reply(
        await write_digest_html(facts),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=digest_keyboard(message.chat.id),
    )


@router.message(lambda message: _is_exact(message, "роли"))
async def handle_roles(message: types.Message):
    if not await _ensure(message):
        return
    await send_roles(message, message.chat.id)


@router.message(lambda message: _is_exact(message, "фракции"))
@ai_feature("фракции")
async def handle_factions(message: types.Message):
    if not await _ensure(message):
        return
    await send_factions(message, message.chat.id)


def _ints(parts: list[str], count: int) -> tuple[int, ...] | None:
    if len(parts) < count:
        return None
    try:
        return tuple(int(item) for item in parts[:count])
    except ValueError:
        return None


def _cringe_on_cooldown(chat_id: int) -> int:
    now = time.monotonic()
    last = _cringe_button_used_at.get(int(chat_id))
    if last is not None and now - last < CRINGE_BUTTON_COOLDOWN_SECONDS:
        return int(CRINGE_BUTTON_COOLDOWN_SECONDS - (now - last))
    _cringe_button_used_at[int(chat_id)] = now
    return 0


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def handle_social_button(query: types.CallbackQuery):
    message = query.message
    if message is None or not _allowed_user(query.from_user):
        await query.answer()
        return
    if not _is_group_chat(message.chat) or not is_social_graph_enabled(message.chat.id):
        await query.answer("Соцграф в этом чате отключён.", show_alert=True)
        return

    action, *args = (query.data or "")[len(CALLBACK_PREFIX):].split(":")
    chat_id = message.chat.id
    try:
        if action == "map":
            await query.answer()
            await graph_handlers.send_social_graph(message, chat_id)
        elif action == "fac":
            await query.answer("Делю чат на лагеря…")
            await send_factions(message, chat_id)
        elif action == "roles":
            await query.answer()
            await send_roles(message, chat_id)
        elif action == "top":
            await query.answer()
            await graph_handlers.send_chat_relationships(message, chat_id)
        elif action == "chr":
            await query.answer()
            if not await send_chronicle_page(message, mode=MODE_RECENT):
                await message.reply("📜 В Летописи пока пусто.")
        elif action == "cringe":
            wait = _cringe_on_cooldown(chat_id)
            if wait:
                await query.answer(f"Paint ещё не остыл, подожди {wait} с.", show_alert=True)
                return
            await query.answer("Рисую…")
            await graph_handlers.send_cringe_graph(message, chat_id)
        elif action in {"hist", "ep"} and (pair := _ints(args, 2)):
            await query.answer()
            if action == "hist":
                await graph_handlers.send_relationship_history(message, chat_id, *pair)
            else:
                await graph_handlers.send_pair_episodes(message, chat_id, *pair)
        elif action == "dos" and (user := _ints(args, 1)):
            await query.answer()
            await send_dossier(message, chat_id, user[0], "Участник")
        elif action == "rels" and (user := _ints(args, 1)):
            await query.answer()
            dossier_name = await _known_name(chat_id, user[0])
            await graph_handlers.send_user_relationships(message, chat_id, user[0], subject_name=dossier_name)
        elif action == "uchr" and (user := _ints(args, 1)):
            await query.answer()
            if not await send_chronicle_page(message, user_id=user[0], target_label=await _known_name(chat_id, user[0])):
                await message.reply("📜 Этот гражданин в Летопись пока не попадал.")
        else:
            await query.answer()
    except Exception as exc:
        logging.error("[social_graph] button %s failed: %s", query.data, exc, exc_info=True)


async def _known_name(chat_id: int, user_id: int) -> str:
    data = await get_graph_data(chat_id)
    return data.names.get(int(user_id), "Участник")
