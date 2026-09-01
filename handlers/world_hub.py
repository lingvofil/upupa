"""Interactive World of Upupa hub, map, news, chronicle and metadata commands."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.loader import bot
from core.upupa_utils import normalize_upupa_command
from features.world.activity import get_top_active_citizen
from features.world.authority import authority_from_counts, calculate_authority
from features.world.identity import ensure_state_identities, ensure_state_identity
from features.world.news import SIGNIFICANT_EVENT_TYPES, format_event_fact, generate_world_news
from features.world.permissions import is_chat_admin, is_strict_chat_admin
from features.world.presentation import format_world_profile
from features.world.rendering import render_world_map_png_async
from features.world.service import format_diplomacy, get_world_service
from handlers.world import _require_world, _title


router = Router(name="world_hub")


def _normalized(message: types.Message) -> str:
    return normalize_upupa_command(message.text or "")


def _is_hub_command(message: types.Message) -> bool:
    return bool(message.text and _normalized(message) == "упупа миры")


def _is_exact(message: types.Message, *commands: str) -> bool:
    if not message.text:
        return False
    raw = message.text.strip().lower()
    normalized = _normalized(message)
    return raw in commands or normalized in commands


def _main_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏳 Моё государство", callback_data="worldhub:mine")
    builder.button(text="🌐 Государства", callback_data="worldhub:states")
    builder.button(text="🤝 Дипломатия", callback_data="worldhub:diplomacy")
    builder.button(text="🗺 Карта мира", callback_data="worldhub:map")
    builder.button(text="📰 Мировые новости", callback_data="worldhub:news")
    builder.button(text="📜 Хроника", callback_data="worldhub:chronicle")
    builder.adjust(2)
    return builder.as_markup()


def _back_markup(target: str = "main") -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В Мир Упупы", callback_data=f"worldhub:{target}")
    return builder.as_markup()


def _state_list_markup(states) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for state in states[:40]:
        label = " ".join(state.title.split())
        if len(label) > 26:
            label = label[:25].rstrip() + "…"
        builder.button(
            text=f"№{state.world_id} · {label}",
            callback_data=f"worldhub:state:{state.world_id}",
        )
    builder.button(text="⬅️ Назад", callback_data="worldhub:main")
    builder.adjust(1)
    return builder.as_markup()


async def _alliance_names(service, profile) -> dict[int, str]:
    if not profile.allies:
        return {}
    names = await asyncio.gather(
        *(service.get_alliance_name(profile.state.world_id, ally.world_id) for ally in profile.allies)
    )
    return {
        ally.world_id: name
        for ally, name in zip(profile.allies, names)
        if name
    }


async def _state_card(world_id: int) -> str | None:
    service = get_world_service()
    profile = await service.get_profile_by_world_id(world_id)
    if profile is None:
        return None
    identity, top_active, alliance_names = await asyncio.gather(
        ensure_state_identity(service, profile.state),
        get_top_active_citizen(profile.state.chat_id),
        _alliance_names(service, profile),
    )
    details = identity.details if identity is not None else await service.get_details(world_id)
    population = None
    try:
        population = await bot.get_chat_member_count(profile.state.chat_id)
    except Exception as exc:
        logging.warning(
            "World population lookup failed chat_id=%s: %s",
            profile.state.chat_id,
            exc,
        )
    most_active = None
    if top_active:
        name, count = top_active
        most_active = f"{name} ({count} сообщ.)"
    return format_world_profile(
        profile,
        population,
        details=details,
        authority=calculate_authority(profile),
        most_active=most_active,
        alliance_names=alliance_names,
    )


async def _states_overview() -> tuple[str, tuple]:
    service = get_world_service()
    states = await service.list_all_states()
    relations = await service.list_relations(active_only=True)
    allies = {state.world_id: 0 for state in states}
    wars = {state.world_id: 0 for state in states}
    for relation in relations:
        target = allies if relation.relation == "allied" else wars
        target[relation.state_a] = target.get(relation.state_a, 0) + 1
        target[relation.state_b] = target.get(relation.state_b, 0) + 1

    identities = await ensure_state_identities(service, states)
    lines = ["🌐 Государства Мира Упупы", ""]
    for state, identity in zip(states, identities):
        authority = authority_from_counts(
            allies.get(state.world_id, 0),
            wars.get(state.world_id, 0),
        )
        lines.append(f"№{state.world_id} — {state.title}")
        if identity is not None:
            lines.append(f"🏛 {identity.details.government_form}")
            lines.append(f"🎩 Посол: {identity.details.ambassador_name or 'не назначен'}")
        else:
            lines.append("🎩 Посол: не назначен")
        lines.append(
            f"🌐 авторитет {authority} · 🤝 {allies.get(state.world_id, 0)} · ⚔️ {wars.get(state.world_id, 0)}"
        )
        lines.append("")
    if not states:
        lines.append("Здесь пока никого. Геополитика не состоялась.")
    return "\n".join(lines).rstrip(), states


async def _diplomacy_text(chat_id: int, title: str) -> str | None:
    service = get_world_service()
    profile = await service.get_profile(chat_id, title)
    if profile is None:
        return None
    names = await _alliance_names(service, profile)
    return format_diplomacy(profile, names)


async def _chronicle_text() -> str:
    service = get_world_service()
    states = await service.list_all_states()
    state_map = {state.world_id: state for state in states}
    events = await service.list_events(
        limit=25,
        event_types=SIGNIFICANT_EVENT_TYPES | {"alliance_proposed", "alliance_rejected"},
    )
    if not events:
        return "📜 В мировой летописи пока подозрительно чисто."
    lines = ["📜 Хроника Мира Упупы", ""]
    for event in events:
        lines.append(
            f"{event.created_at.strftime('%d.%m.%Y %H:%M')} — {format_event_fact(event, state_map)}"
        )
    return "\n".join(lines)


async def _send_map(message: types.Message) -> None:
    service = get_world_service()
    states, relations = await asyncio.gather(
        service.list_all_states(),
        service.list_relations(active_only=True),
    )
    if not states:
        await message.answer("🗺 Карта не нарисовалась: государств ещё нет.")
        return
    png = await render_world_map_png_async(states, relations)
    await message.answer_photo(
        BufferedInputFile(png, filename="world_upupa.png"),
        caption=(
            "🗺 Мир Упупы. Зелёные линии — союзы, красные — войны. "
            "Число внутри государства — международный авторитет."
        ),
    )


async def _is_diplomat(chat_id: int, user_id: int) -> bool:
    return await is_chat_admin(bot, chat_id, user_id)


@router.message(_is_hub_command)
async def world_hub(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    state = await service.get_state(message.chat.id, _title(message))
    profile = await service.get_profile(message.chat.id, _title(message))
    if state is None or profile is None:
        return
    text = (
        "🌍 Мир Упупы\n\n"
        f"Вы — государство №{state.world_id} «{state.title}».\n"
        f"Международный авторитет: {calculate_authority(profile)}.\n\n"
        "Куда полезем?"
    )
    await message.reply(text, reply_markup=_main_markup())


@router.message(lambda message: _is_exact(message, "государство"))
async def enhanced_state_profile(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    state = await service.get_state(message.chat.id, _title(message))
    if state is None:
        return
    text = await _state_card(state.world_id)
    if text:
        await message.reply(text, reply_markup=_back_markup())


@router.message(lambda message: _is_exact(message, "государства"))
async def enhanced_states_list(message: types.Message):
    if await _require_world(message) is None:
        return
    text, states = await _states_overview()
    await message.reply(text, reply_markup=_state_list_markup(states))


@router.message(lambda message: _is_exact(message, "дипломатия"))
async def enhanced_diplomacy(message: types.Message):
    if await _require_world(message) is None:
        return
    text = await _diplomacy_text(message.chat.id, _title(message))
    if text:
        await message.reply(text, reply_markup=_back_markup())


@router.message(lambda message: _is_exact(message, "карта мира", "упупа карта мира"))
async def world_map_command(message: types.Message):
    if await _require_world(message) is None:
        return
    await _send_map(message)


@router.message(lambda message: _is_exact(message, "мировые новости", "упупа мировые новости"))
async def world_news_command(message: types.Message):
    if await _require_world(message) is None:
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    text = await generate_world_news(str(message.chat.id))
    await message.reply(f"📰 {text}", reply_markup=_back_markup())


@router.message(lambda message: _is_exact(message, "хроника", "упупа хроника"))
async def world_chronicle_command(message: types.Message):
    if await _require_world(message) is None:
        return
    await message.reply(await _chronicle_text(), reply_markup=_back_markup())


@router.message(
    lambda message: bool(message.text)
    and _normalized(message).startswith("упупа назначь посла")
)
async def appoint_ambassador(message: types.Message):
    service = await _require_world(message)
    if service is None or message.from_user is None:
        return
    if not await is_strict_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("🎩 Назначать посла могут только администраторы государства.")
        return
    replied = message.reply_to_message
    target = replied.from_user if replied is not None else None
    if target is None or target.is_bot:
        await message.reply("🎩 Реплайни этой командой на сообщение гражданина, которого назначаем послом.")
        return
    state = await service.get_state(message.chat.id, _title(message))
    if state is None:
        return
    name = target.full_name or (f"@{target.username}" if target.username else str(target.id))
    await service.set_ambassador(state.world_id, target.id, name)
    await message.reply(
        f"🎩 {name} назначен послом государства №{state.world_id}. "
        "Теперь ему доступны дипломатические решения наравне с админами."
    )


@router.message(
    lambda message: bool(message.text)
    and _normalized(message) in {"упупа сними посла", "упупа убери посла"}
)
async def remove_ambassador(message: types.Message):
    service = await _require_world(message)
    if service is None or message.from_user is None:
        return
    if not await is_strict_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("🎩 Снимать посла могут только администраторы государства.")
        return
    state = await service.get_state(message.chat.id, _title(message))
    if state is None:
        return
    await service.set_ambassador(state.world_id, None, None)
    await message.reply("🎩 Посол снят. Министерство иностранных дел снова состоит из админов.")


@router.message(
    lambda message: bool(message.text)
    and _normalized(message).startswith("упупа назови союз")
)
async def name_alliance(message: types.Message):
    service = await _require_world(message)
    if service is None or message.from_user is None:
        return
    if not await _is_diplomat(message.chat.id, message.from_user.id):
        await message.reply("🤝 Называть союзы могут администраторы или посол.")
        return

    normalized = _normalized(message)
    rest = normalized.removeprefix("упупа назови союз").strip()
    parts = rest.split(maxsplit=1)
    if len(parts) != 2:
        await message.reply("Формат: упупа назови союз <номер государства> <название>")
        return
    try:
        target_world_id = int(parts[0].lstrip("№#"))
    except ValueError:
        await message.reply("Не понял номер государства. Формат: упупа назови союз 7 Пивной пакт")
        return
    status, source, target, clean = await service.name_alliance(
        message.chat.id,
        _title(message),
        target_world_id,
        parts[1],
    )
    if status == "unknown_target":
        await message.reply("Такого государства нет.")
    elif status == "self":
        await message.reply("Союз с самим собой уже называется «этот чат».")
    elif status == "not_allied":
        await message.reply("Сначала заключите союз с этим государством.")
    elif status == "empty":
        await message.reply("Название должно содержать хотя бы что-нибудь, кроме воздуха.")
    elif status == "named" and source and target and clean:
        await message.reply(f"🤝 Союз с государством №{target.world_id} теперь называется «{clean}».")
        if target.enabled:
            try:
                await bot.send_message(
                    target.chat_id,
                    f"🤝 Ваш союз с государством №{source.world_id} получил название «{clean}».",
                )
            except Exception:
                logging.exception("World alliance name notification failed target=%s", target.world_id)
    else:
        await message.reply("Не удалось переименовать этот геополитический кружок.")


@router.callback_query(F.data.startswith("worldhub:"))
async def world_hub_callback(query: types.CallbackQuery):
    if query.message is None or query.from_user is None:
        return
    service = get_world_service()
    state = await service.get_state(query.message.chat.id, query.message.chat.title or "Безымянное государство")
    if state is None or not state.enabled:
        await query.answer("Этот чат сейчас вне Мира Упупы.", show_alert=True)
        return

    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else "main"

    if action == "main":
        profile = await service.get_profile(query.message.chat.id, query.message.chat.title)
        authority = calculate_authority(profile) if profile else 50
        text = (
            "🌍 Мир Упупы\n\n"
            f"Вы — государство №{state.world_id} «{state.title}».\n"
            f"Международный авторитет: {authority}.\n\n"
            "Куда полезем?"
        )
        await query.message.edit_text(text, reply_markup=_main_markup())
    elif action == "mine":
        text = await _state_card(state.world_id)
        if text:
            await query.message.edit_text(text, reply_markup=_back_markup())
    elif action == "states":
        text, states = await _states_overview()
        await query.message.edit_text(text, reply_markup=_state_list_markup(states))
    elif action == "state" and len(parts) >= 3:
        try:
            world_id = int(parts[2])
        except ValueError:
            await query.answer("Кривой номер государства.", show_alert=True)
            return
        text = await _state_card(world_id)
        if text is None:
            await query.answer("Государство сейчас недоступно.", show_alert=True)
            return
        await query.message.edit_text(text, reply_markup=_back_markup("states"))
    elif action == "diplomacy":
        text = await _diplomacy_text(query.message.chat.id, query.message.chat.title or "Безымянное государство")
        if text:
            await query.message.edit_text(text, reply_markup=_back_markup())
    elif action == "chronicle":
        await query.message.edit_text(await _chronicle_text(), reply_markup=_back_markup())
    elif action == "news":
        await query.answer("Редакция международного отдела проснулась")
        text = await generate_world_news(str(query.message.chat.id))
        await query.message.edit_text(f"📰 {text}", reply_markup=_back_markup())
        return
    elif action == "map":
        await query.answer("Рисую границы, которых не существует")
        await _send_map(query.message)
        return
    else:
        await query.answer("Неизвестный раздел Мира Упупы.", show_alert=True)
        return

    await query.answer()
