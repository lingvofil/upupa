"""World of Upupa hub: every screen of the main menu and the read-only commands.

This is the only router that renders hub screens (``worldhub:*``); diplomatic
actions live in ``world_interactions``, sanctions/court commands in
``world_expansion`` and symbols in ``world_symbols``.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.loader import bot
from core.upupa_utils import normalize_upupa_command
from features.world.battles import active_battle_lines, battle_wins
from features.world.blocs import bloc_of, format_blocs, list_blocs
from features.world.cards import build_state_card, symbol_store
from features.world.expansion import (
    format_international_cases,
    format_sanctions,
    list_international_cases,
    list_state_sanctions,
)
from features.world.hub_ui import (
    STATES_PAGE_SIZE,
    back_markup,
    blocs_markup,
    build_world_main_markup,
    diplomacy_markup,
    own_state_markup,
    paged_markup,
    sanctions_markup,
    settings_markup,
)
from features.world.identity import cached_state_identities
from features.world.news import SIGNIFICANT_EVENT_TYPES, format_event_fact, generate_world_news
from features.world.presentation import (
    format_diplomacy,
    format_hub_text,
    format_rating,
    short_title,
)
from features.world.rendering import render_world_map_png_async
from features.world.service import get_world_service
from handlers.world_common import (
    answer_quietly,
    callback_state,
    is_admin,
    reply_long,
    require_world,
    safe_edit,
    single_flight,
    user_label,
)


router = Router(name="world_hub")
_FLAG_CACHE: dict[str, bytes] = {}
_FLAG_CACHE_LIMIT = 64


def _normalized(message: types.Message) -> str:
    return normalize_upupa_command(message.text or "")


def _is_exact(message: types.Message, *commands: str) -> bool:
    if not message.text:
        return False
    return message.text.strip().lower() in commands or _normalized(message) in commands


def _main_markup() -> types.InlineKeyboardMarkup:
    return build_world_main_markup()


# --------------------------------------------------------------------- texts


async def _hub_text(service, state) -> str:
    return format_hub_text(state, await service.get_authority(state.world_id))


async def _states_screen(service, current_world_id: int, page: int = 0) -> tuple[str, types.InlineKeyboardMarkup]:
    states = list(await service.list_all_states())
    authority = await service.get_authority_map()
    _markup, page, pages = paged_markup(
        states,
        page=page,
        page_size=STATES_PAGE_SIZE,
        label=lambda state: f"№{state.world_id} · {short_title(state.title)}",
        callback=lambda state: f"worldhub:state:{state.world_id}",
        nav_prefix="worldhub:states",
        tail=[("⬅️ Назад", "worldhub:main")],
    )
    visible = tuple(states[page * STATES_PAGE_SIZE:(page + 1) * STATES_PAGE_SIZE])
    identities = await cached_state_identities(service, visible)
    details = await service.list_details()
    lines = [f"🌐 Государства Мира Упупы · стр. {page + 1}/{pages}", ""]
    if not states:
        lines.append("Здесь пока никого. Геополитика не состоялась.")
    for state, identity in zip(visible, identities):
        marker = " ← вы" if state.world_id == current_world_id else ""
        breakdown = authority.get(state.world_id)
        lines.append(f"№{state.world_id} — {state.title}{marker}")
        state_details = details.get(state.world_id)
        visible_identity = state.world_id == current_world_id or (state_details is None or state_details.share_identity)
        if identity is not None and visible_identity:
            lines.append(f"🏛 {identity.details.government_form}")
        ambassador = state_details.ambassador_name if state_details else None
        lines.append(f"🎩 Посол: {ambassador or 'не назначен'}")
        lines.append(f"🌐 авторитет {breakdown.total if breakdown else 50}")
        lines.append("")
    return "\n".join(lines).rstrip(), _markup


async def _diplomacy_text(service, state) -> str | None:
    profile = await service.get_profile(state.chat_id)
    if profile is None:
        return None
    names, battles = await asyncio.gather(
        service.get_alliance_names(state.world_id),
        active_battle_lines(service, state.world_id),
    )
    return format_diplomacy(profile, names, battle_lines=battles)


async def _state_names(service) -> dict[int, str]:
    return {state.world_id: state.title for state in await service.list_all_states()}


async def _sanctions_text(service, state) -> str:
    sanctions = await list_state_sanctions(state.world_id, service)
    return (
        format_sanctions(sanctions, state.world_id, await _state_names(service))
        + "\n\nКнопками или текстом: «упупа санкции <№> [причина]», «упупа снять санкции <№>»."
    )


async def _court_text(service) -> str:
    cases = await list_international_cases(service=service)
    return (
        format_international_cases(cases, await _state_names(service))
        + "\n\nПодать иск: «упупа международный суд <№> <претензия>». "
        "Ответчик получит повестку и час на защиту; исход суда влияет на авторитет."
    )


async def _chronicle_text(service) -> str:
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
        lines.append(f"{event.created_at.strftime('%d.%m.%Y %H:%M')} — {format_event_fact(event, state_map)}")
    return "\n".join(lines)


async def _rating_text(service, state) -> str:
    states, authority, wins = await asyncio.gather(
        service.list_all_states(),
        service.get_authority_map(),
        battle_wins(service),
    )
    tourism = await asyncio.to_thread(service.ledger.tourism_ratings) if service.ledger else {}
    return format_rating(
        states,
        authority,
        tourism=tourism,
        battle_wins=wins,
        current_world_id=state.world_id,
    )


async def _blocs_screen(service, state) -> tuple[str, types.InlineKeyboardMarkup]:
    blocs, states = await asyncio.gather(list_blocs(service), service.list_all_states())
    own = await bloc_of(service, state.world_id)
    text = format_blocs(blocs, {item.world_id: item for item in states}, state.world_id)
    return text, blocs_markup(member=own is not None, can_apply=bool(blocs))


async def _settings_screen(service, state) -> tuple[str, types.InlineKeyboardMarkup]:
    details = await service.get_details(state.world_id)
    share_identity = bool(details and details.share_identity)
    share_activity = bool(details and details.share_activity)
    newspaper = bool(details is None or details.newspaper)
    text = (
        "⚙️ Настройки государства\n\n"
        "Характеристика — строй, климат и угроза, которые Упупа выводит из жизни чата.\n"
        "Активность — самый активный гражданин, число сообщений и социальные связи.\n"
        "Свою карточку государство всегда видит полностью; переключатели решают, что видят другие.\n\n"
        "Менять настройки могут только администраторы."
    )
    return text, settings_markup(
        share_identity=share_identity,
        share_activity=share_activity,
        newspaper=newspaper,
    )


async def _ambassador_screen(service, state) -> tuple[str, types.InlineKeyboardMarkup]:
    details = await service.get_details(state.world_id)
    current = details.ambassador_name if details and details.ambassador_name else "не назначен"
    text = (
        "🎩 Посол государства\n\n"
        f"Сейчас: {current}.\n\n"
        "Посол принимает дипломатические решения наравне с админами: союзы, войны, битвы, мир, визиты, "
        "санкции, иски и блоки. Чтобы назначить, админ отвечает на сообщение нужного человека командой "
        "«упупа назначь посла». Снять — «упупа сними посла» или кнопкой."
    )
    builder = InlineKeyboardBuilder()
    if details and details.ambassador_user_id:
        builder.button(text="🎩 Снять посла", callback_data="worldhub:ambassador_remove")
    builder.button(text="⬅️ К государству", callback_data="worldhub:mine")
    builder.adjust(1)
    return text, builder.as_markup()


async def _flag_bytes(file_id: str) -> bytes | None:
    if file_id in _FLAG_CACHE:
        return _FLAG_CACHE[file_id]
    try:
        buffer = BytesIO()
        await asyncio.wait_for(bot.download(file_id, destination=buffer), timeout=15)
        data = buffer.getvalue()
    except Exception as exc:
        logging.warning("World map flag download failed: %s", exc)
        return None
    if len(_FLAG_CACHE) >= _FLAG_CACHE_LIMIT:
        _FLAG_CACHE.pop(next(iter(_FLAG_CACHE)))
    _FLAG_CACHE[file_id] = data
    return data


async def _send_map(message: types.Message, service) -> None:
    states, relations, authority, blocs = await asyncio.gather(
        service.list_all_states(),
        service.list_relations(active_only=True),
        service.get_authority_map(),
        list_blocs(service),
    )
    if not states:
        await message.answer("🗺 Карта не нарисовалась: государств ещё нет.")
        return
    store = symbol_store(service)
    symbols = await asyncio.to_thread(store.list_flags) if store else {}
    enabled_ids = {state.world_id for state in states}
    semaphore = asyncio.Semaphore(4)

    async def load(world_id: int, file_id: str):
        async with semaphore:
            return world_id, await _flag_bytes(file_id)

    loaded = await asyncio.gather(
        *(load(world_id, symbol.telegram_file_id) for world_id, symbol in symbols.items() if world_id in enabled_ids)
    )
    flags = {world_id: data for world_id, data in loaded if data}
    bloc_index = {member: bloc.bloc_id for bloc in blocs for member in bloc.members}
    png = await render_world_map_png_async(
        states,
        relations,
        authority={world_id: breakdown.total for world_id, breakdown in authority.items()},
        flags=flags,
        blocs=bloc_index,
    )
    await message.answer_photo(
        BufferedInputFile(png, filename="world_upupa.png"),
        caption=(
            "🗺 Мир Упупы. Зелёные линии — союзы, красные — войны, цветные кольца — блоки. "
            "Число у государства — международный авторитет."
        ),
    )


async def _send_card(message: types.Message, service, state, *, viewer_world_id: int, edit: bool) -> None:
    card = await build_state_card(service, state.world_id, bot=bot, viewer_world_id=viewer_world_id)
    if card is None:
        await message.answer("Государство сейчас недоступно.")
        return
    markup = own_state_markup(has_symbol=card.flag_file_id is not None) if card.own else back_markup("states", "⬅️ К списку")
    if edit:
        await safe_edit(message, card.text, markup)
    else:
        await reply_long(message, card.text, markup)


# ------------------------------------------------------------------ messages


@router.message(F.migrate_to_chat_id)
async def world_chat_migrated(message: types.Message):
    """A group became a supergroup: the state follows its new chat id."""
    try:
        await get_world_service().migrate_chat(message.chat.id, int(message.migrate_to_chat_id))
    except RuntimeError:
        pass


@router.message(F.new_chat_title)
async def world_chat_renamed(message: types.Message):
    try:
        service = get_world_service()
    except RuntimeError:
        return
    if await service.get_state(message.chat.id) is not None:
        await service.get_state(message.chat.id, message.new_chat_title)


@router.message(lambda message: bool(message.text) and _normalized(message) == "упупа миры")
async def world_hub(message: types.Message):
    resolved = await require_world(message, refresh_title=True)
    if resolved is None:
        return
    service, state = resolved
    await message.reply(await _hub_text(service, state), reply_markup=_main_markup())


@router.message(lambda message: _is_exact(message, "государство", "упупа государство"))
async def own_state_card(message: types.Message):
    resolved = await require_world(message, refresh_title=True)
    if resolved is None:
        return
    service, state = resolved
    card = await build_state_card(service, state.world_id, bot=bot, viewer_world_id=state.world_id)
    if card is None:
        return
    markup = own_state_markup(has_symbol=card.flag_file_id is not None)
    if card.flag_file_id and len(card.text) <= 1024:
        try:
            await message.reply_photo(card.flag_file_id, caption=card.text, reply_markup=markup)
            return
        except Exception as exc:
            logging.warning("World card with flag failed, falling back to text: %s", exc)
    await reply_long(message, card.text, markup)


@router.message(lambda message: _is_exact(message, "государства", "упупа государства"))
async def states_list(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    service, state = resolved
    text, markup = await _states_screen(service, state.world_id)
    await reply_long(message, text, markup)


@router.message(lambda message: _is_exact(message, "дипломатия", "упупа дипломатия"))
async def diplomacy(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    service, state = resolved
    text = await _diplomacy_text(service, state)
    if text:
        await reply_long(message, text, diplomacy_markup())


@router.message(lambda message: _is_exact(message, "карта мира", "упупа карта мира"))
async def world_map_command(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    async with single_flight(f"map:{message.chat.id}") as acquired:
        if acquired:
            await _send_map(message, resolved[0])


@router.message(lambda message: _is_exact(message, "мировые новости", "упупа мировые новости"))
async def world_news_command(message: types.Message):
    if await require_world(message) is None:
        return
    async with single_flight(f"news:{message.chat.id}") as acquired:
        if not acquired:
            return
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        text = await generate_world_news(str(message.chat.id))
        await reply_long(message, f"📰 {text}", back_markup())


@router.message(lambda message: _is_exact(message, "хроника", "упупа хроника"))
async def world_chronicle_command(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    await reply_long(message, await _chronicle_text(resolved[0]), back_markup())


@router.message(lambda message: _is_exact(message, "рейтинг мира", "упупа рейтинг мира", "упупа рейтинг государств"))
async def world_rating_command(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    service, state = resolved
    await reply_long(message, await _rating_text(service, state), back_markup())


@router.message(lambda message: _is_exact(message, "блоки", "упупа блоки"))
async def world_blocs_command(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    text, markup = await _blocs_screen(*resolved)
    await reply_long(message, text, markup)


@router.message(lambda message: bool(message.text) and _normalized(message).startswith("упупа назначь посла"))
async def appoint_ambassador(message: types.Message):
    resolved = await require_world(message)
    if resolved is None or message.from_user is None:
        return
    service, state = resolved
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("🎩 Назначать посла могут только администраторы государства.")
        return
    replied = message.reply_to_message
    target = replied.from_user if replied is not None else None
    if target is None or target.is_bot:
        await message.reply("🎩 Реплайни этой командой на сообщение гражданина, которого назначаем послом.")
        return
    name = user_label(target)
    await service.set_ambassador(state.world_id, target.id, name)
    await message.reply(
        f"🎩 {name} назначен послом государства №{state.world_id}. "
        "Теперь ему доступны дипломатические решения наравне с админами."
    )


@router.message(
    lambda message: bool(message.text) and _normalized(message) in {"упупа сними посла", "упупа убери посла"}
)
async def remove_ambassador(message: types.Message):
    resolved = await require_world(message)
    if resolved is None or message.from_user is None:
        return
    service, state = resolved
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("🎩 Снимать посла могут только администраторы государства.")
        return
    await service.set_ambassador(state.world_id, None, None)
    await message.reply("🎩 Посол снят. Министерство иностранных дел снова состоит из админов.")


# ----------------------------------------------------------------- callbacks


@router.callback_query(F.data.startswith("worldhub:") | F.data.startswith("worldx:ambassador"))
async def world_hub_callback(query: types.CallbackQuery):
    resolved = await callback_state(query)
    if resolved is None:
        return
    service, state = resolved
    data = query.data or ""
    if data.startswith("worldx:ambassador"):
        # Buttons from messages sent before the menu was reorganised.
        data = "worldhub:ambassador_remove" if data.endswith(":remove") else "worldhub:ambassador"
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "main"
    message = query.message

    if action == "main":
        await safe_edit(message, await _hub_text(service, state), _main_markup())
    elif action == "mine":
        await answer_quietly(query, "Изучаю государство")
        await _send_card(message, service, state, viewer_world_id=state.world_id, edit=True)
        return
    elif action == "states":
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            page = 0
        text, markup = await _states_screen(service, state.world_id, page)
        await safe_edit(message, text, markup)
    elif action == "state" and len(parts) >= 3:
        try:
            world_id = int(parts[2])
        except ValueError:
            await query.answer("Кривой номер государства.", show_alert=True)
            return
        card = await build_state_card(service, world_id, bot=bot, viewer_world_id=state.world_id)
        if card is None:
            await query.answer("Государство сейчас недоступно.", show_alert=True)
            return
        markup = (
            own_state_markup(has_symbol=card.flag_file_id is not None)
            if card.own
            else back_markup("states", "⬅️ К списку")
        )
        await safe_edit(message, card.text, markup)
    elif action == "diplomacy":
        text = await _diplomacy_text(service, state)
        if text:
            await safe_edit(message, text, diplomacy_markup())
    elif action == "sanctions":
        await safe_edit(message, await _sanctions_text(service, state), sanctions_markup())
    elif action == "court":
        await safe_edit(message, await _court_text(service), back_markup())
    elif action == "chronicle":
        await safe_edit(message, await _chronicle_text(service), back_markup())
    elif action == "rating":
        await safe_edit(message, await _rating_text(service, state), back_markup())
    elif action == "blocs":
        text, markup = await _blocs_screen(service, state)
        await safe_edit(message, text, markup)
    elif action == "news":
        async with single_flight(f"news:{message.chat.id}") as acquired:
            if not acquired:
                await query.answer("Редакция уже пишет выпуск, подожди.")
                return
            await answer_quietly(query, "Редакция международного отдела проснулась")
            text = await generate_world_news(str(message.chat.id))
            await safe_edit(message, f"📰 {text}", back_markup())
        return
    elif action == "map":
        async with single_flight(f"map:{message.chat.id}") as acquired:
            if not acquired:
                await query.answer("Карта уже рисуется.")
                return
            await answer_quietly(query, "Рисую границы, которых не существует")
            await _send_map(message, service)
        return
    elif action == "flag":
        store = symbol_store(service)
        symbols = await asyncio.to_thread(store.get_symbols, state.world_id) if store else {}
        if not symbols:
            await query.answer("Официальной символики пока нет.", show_alert=True)
            return
        await query.answer()
        for kind in ("flag", "emblem"):
            symbol = symbols.get(kind)
            if symbol is None:
                continue
            label = "🏳 Официальный флаг" if kind == "flag" else "🛡 Официальный герб"
            await message.answer_photo(
                symbol.telegram_file_id,
                caption=f"{label} государства №{state.world_id} «{state.title}».",
            )
        return
    elif action == "settings":
        text, markup = await _settings_screen(service, state)
        await safe_edit(message, text, markup)
    elif action == "set" and len(parts) >= 3:
        if not await is_admin(message.chat.id, query.from_user.id):
            await query.answer("Настройки государства меняют только администраторы.", show_alert=True)
            return
        key = parts[2]
        details = await service.get_details(state.world_id)
        if details is None or key not in {"share_identity", "share_activity", "newspaper"}:
            await query.answer("Неизвестная настройка.", show_alert=True)
            return
        await service.set_setting(state.world_id, key, not bool(getattr(details, key)))
        text, markup = await _settings_screen(service, state)
        await safe_edit(message, text, markup)
    elif action == "ambassador":
        text, markup = await _ambassador_screen(service, state)
        await safe_edit(message, text, markup)
    elif action == "ambassador_remove":
        if not await is_admin(message.chat.id, query.from_user.id):
            await query.answer("Снимать посла могут только администраторы государства.", show_alert=True)
            return
        await service.set_ambassador(state.world_id, None, None)
        text, markup = await _ambassador_screen(service, state)
        await safe_edit(message, text, markup)
    else:
        await query.answer("Неизвестный раздел Мира Упупы.", show_alert=True)
        return
    await answer_quietly(query)

