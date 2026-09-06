"""Telegram transport for measured World characteristics, sanctions and court."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.loader import bot
from core.upupa_utils import normalize_upupa_command
from features.world.expansion import (
    build_expanded_state_card,
    format_international_cases,
    format_sanctions,
    get_state_characteristics,
    impose_sanctions,
    international_court,
    lift_sanctions,
    list_international_cases,
    list_state_sanctions,
)
from features.world.permissions import is_chat_admin
from features.world.service import format_diplomacy, get_world_service
from handlers.world import _require_world, _title


router = Router(name="world_expansion")


def _normalized(message: types.Message) -> str:
    return normalize_upupa_command(message.text or "")


def _back_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    return builder.as_markup()


def _main_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏳 Моё государство", callback_data="worldhub:mine")
    builder.button(text="🌐 Государства", callback_data="worldhub:states")
    builder.button(text="🤝 Дипломатия", callback_data="worldhub:diplomacy")
    builder.button(text="🚫 Санкции", callback_data="worldhub:sanctions")
    builder.button(text="⚖️ Международный суд", callback_data="worldhub:court")
    builder.button(text="🗺 Карта мира", callback_data="worldhub:map")
    builder.button(text="📰 Мировые новости", callback_data="worldhub:news")
    builder.button(text="📜 Хроника", callback_data="worldhub:chronicle")
    builder.adjust(2)
    return builder.as_markup()


async def _is_diplomat(chat_id: int, user_id: int) -> bool:
    service = get_world_service()
    return await is_chat_admin(bot, chat_id, user_id) or await service.is_ambassador(chat_id, user_id)


async def _current_state(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return None, None
    return service, await service.get_state(message.chat.id, _title(message))


def _parse_target_tail(normalized: str, prefix: str) -> tuple[int | None, str]:
    tail = normalized.removeprefix(prefix).strip()
    if not tail:
        return None, ""
    parts = tail.split(maxsplit=1)
    try:
        world_id = int(parts[0].lstrip("№#"))
    except ValueError:
        return None, tail
    return world_id, parts[1].strip() if len(parts) > 1 else ""


async def _state_names() -> dict[int, str]:
    states = await get_world_service().list_all_states()
    return {state.world_id: state.title for state in states}


async def _hub_text(service, state, title: str) -> str:
    profile = await service.get_profile(state.chat_id, title)
    if profile is None:
        authority = 50
    else:
        authority = (await get_state_characteristics(profile)).effective_authority
    return (
        "🌍 Мир Упупы\n\n"
        f"Вы — государство №{state.world_id} «{state.title}».\n"
        f"Международный авторитет: {authority}.\n\n"
        "Куда полезем?"
    )


@router.message(lambda message: bool(message.text) and _normalized(message) == "упупа миры")
async def expanded_world_hub(message: types.Message):
    service, state = await _current_state(message)
    if service is None or state is None:
        return
    await message.reply(
        await _hub_text(service, state, _title(message)),
        reply_markup=_main_markup(),
    )


@router.message(lambda message: bool(message.text) and _normalized(message) == "государство")
async def expanded_state_profile(message: types.Message):
    service, state = await _current_state(message)
    if service is None or state is None:
        return
    text = await build_expanded_state_card(state.world_id, message.bot)
    if text:
        await message.reply(text, reply_markup=_back_markup())


@router.message(
    lambda message: bool(message.text)
    and _normalized(message) in {"санкции", "упупа санкции"}
)
async def sanctions_status(message: types.Message):
    service, state = await _current_state(message)
    if service is None or state is None:
        return
    sanctions = await list_state_sanctions(state.world_id)
    await message.reply(format_sanctions(sanctions, state.world_id, await _state_names()))


@router.message(
    lambda message: bool(message.text)
    and _normalized(message).startswith("упупа санкции ")
)
async def sanctions_impose(message: types.Message):
    service, state = await _current_state(message)
    if service is None or state is None or message.from_user is None:
        return
    if not await _is_diplomat(message.chat.id, message.from_user.id):
        await message.reply("🚫 Санкции могут вводить только администраторы или посол.")
        return
    target_id, reason = _parse_target_tail(_normalized(message), "упупа санкции")
    if target_id is None:
        await message.reply("Формат: упупа санкции <номер государства> [причина]")
        return
    status, sanction = await impose_sanctions(state.world_id, target_id, reason)
    if status == "self":
        await message.reply("🚫 Самосанкции — это уже внутренняя экономическая политика.")
    elif status == "unknown_target":
        await message.reply("🚫 Такого активного государства нет.")
    elif status == "exists":
        await message.reply("🚫 Эти санкции уже действуют. Министерство повторяется.")
    elif sanction is not None:
        target = await service.get_state_by_world_id(target_id)
        reason_text = f" Причина: {sanction.reason}" if sanction.reason else ""
        await message.reply(f"🚫 Государство №{target_id} попало под санкции.{reason_text}")
        if target is not None:
            try:
                await bot.send_message(
                    target.chat_id,
                    f"🚫 Государство №{state.world_id} ввело против вас санкции.{reason_text}",
                )
            except Exception:
                logging.exception("World sanctions notification failed target=%s", target_id)


@router.message(
    lambda message: bool(message.text)
    and _normalized(message).startswith("упупа снять санкции ")
)
async def sanctions_lift(message: types.Message):
    service, state = await _current_state(message)
    if service is None or state is None or message.from_user is None:
        return
    if not await _is_diplomat(message.chat.id, message.from_user.id):
        await message.reply("🚫 Снимать санкции могут только администраторы или посол.")
        return
    target_id, _reason = _parse_target_tail(_normalized(message), "упупа снять санкции")
    if target_id is None:
        await message.reply("Формат: упупа снять санкции <номер государства>")
        return
    status = await lift_sanctions(state.world_id, target_id)
    await message.reply(
        f"🕊 Санкции против государства №{target_id} сняты. Таможня выдохнула."
        if status == "lifted"
        else "Активных санкций от нас против этого государства не найдено."
    )


@router.message(
    lambda message: bool(message.text)
    and _normalized(message) in {"международный суд", "упупа международный суд"}
)
async def international_practice(message: types.Message):
    if await _require_world(message) is None:
        return
    cases = await list_international_cases()
    await message.reply(format_international_cases(cases, await _state_names()))


@router.message(
    lambda message: bool(message.text)
    and _normalized(message).startswith("упупа международный суд ")
)
async def international_case(message: types.Message):
    service, state = await _current_state(message)
    if service is None or state is None or message.from_user is None:
        return
    if not await _is_diplomat(message.chat.id, message.from_user.id):
        await message.reply("⚖️ Международные иски подают только администраторы или посол.")
        return
    target_id, claim = _parse_target_tail(_normalized(message), "упупа международный суд")
    if target_id is None or not claim:
        await message.reply("Формат: упупа международный суд <номер государства> <претензия>")
        return
    status_message = await message.reply("⚖️ Международный суд изучает летопись и делает очень важное лицо...")
    status, case = await international_court(state.world_id, target_id, claim, str(message.chat.id))
    if status == "unknown_target":
        await status_message.edit_text("⚖️ Ответчик не найден в активном Мире Упупы.")
    elif status == "self":
        await status_message.edit_text("⚖️ Судиться с самим собой можно, но это называется внутренний чат.")
    elif status == "empty":
        await status_message.edit_text("⚖️ Нужна хотя бы формулировка претензии.")
    elif case is None:
        await status_message.edit_text("⚖️ Суд не смог вынести решение. Международное право опять сломалось.")
    else:
        await status_message.edit_text(f"⚖️ Международное дело №{case.case_id}\n\n{case.verdict}")
        defendant = await service.get_state_by_world_id(target_id)
        if defendant is not None:
            try:
                await bot.send_message(
                    defendant.chat_id,
                    f"⚖️ Государство №{state.world_id} подало на вас в Международный суд.\n"
                    f"Дело №{case.case_id}: {case.claim}\n\n{case.verdict}",
                )
            except Exception:
                logging.exception("World court notification failed target=%s", target_id)


@router.callback_query(
    F.data.in_({
        "worldhub:main",
        "worldhub:mine",
        "worldhub:diplomacy",
        "worldhub:sanctions",
        "worldhub:court",
    })
)
async def expanded_hub_sections(query: types.CallbackQuery):
    if query.message is None:
        return
    service = get_world_service()
    state = await service.get_state(query.message.chat.id, query.message.chat.title or "Безымянное государство")
    if state is None or not state.enabled:
        await query.answer("Этот чат сейчас вне Мира Упупы.", show_alert=True)
        return

    if query.data == "worldhub:main":
        await query.message.edit_text(
            await _hub_text(service, state, query.message.chat.title or "Безымянное государство"),
            reply_markup=_main_markup(),
        )
        await query.answer()
        return

    if query.data == "worldhub:mine":
        text = await build_expanded_state_card(state.world_id, query.message.bot)
    elif query.data == "worldhub:sanctions":
        sanctions = await list_state_sanctions(state.world_id)
        text = (
            format_sanctions(sanctions, state.world_id, await _state_names())
            + "\n\nВвести: «упупа санкции <№> [причина]»."
            + "\nСнять: «упупа снять санкции <№>»."
        )
    elif query.data == "worldhub:court":
        cases = await list_international_cases()
        text = (
            format_international_cases(cases, await _state_names())
            + "\n\nПодать иск: «упупа международный суд <№> <претензия>»."
        )
    else:
        profile = await service.get_profile(query.message.chat.id, query.message.chat.title)
        if profile is None:
            text = None
        else:
            sanctions = await list_state_sanctions(state.world_id)
            text = (
                format_diplomacy(profile)
                + "\n\n"
                + format_sanctions(sanctions, state.world_id, await _state_names())
                + "\n\n⚖️ Международный суд доступен отдельной кнопкой в главном меню."
            )
    if text:
        await query.message.edit_text(text, reply_markup=_back_markup())
    await query.answer()
