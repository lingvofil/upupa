"""Interactive diplomatic actions layered on top of the World of Upupa hub."""

from __future__ import annotations

import asyncio
import logging
import math
import re

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from AI.dialog.settings import build_prompt_with_current_chat_prompt
from AI.summarize import _generate_with_active_model
from core.loader import bot
from core.upupa_utils import normalize_upupa_command
from features.world.authority import calculate_authority
from features.world.interactions import insult_cooldown_remaining, record_interaction_event
from features.world.permissions import is_chat_admin, is_strict_chat_admin
from features.world.service import format_diplomacy, get_world_service
from handlers.world import _require_world, _title


router = Router(name="world_interactions")
VISIT_PROMPT_PREFIX = "🛬 Государственный визит"
_VISIT_TARGET_RE = re.compile(r"Гости: государство №(\d+)")


def _normalized(message: types.Message) -> str:
    return normalize_upupa_command(message.text or "")


def _is_hub_command(message: types.Message) -> bool:
    return bool(message.text and _normalized(message) == "упупа миры")


def _is_diplomacy_command(message: types.Message) -> bool:
    if not message.text:
        return False
    raw = message.text.strip().lower()
    return raw == "дипломатия" or _normalized(message) == "дипломатия"


def _is_visit_showcase_reply(message: types.Message) -> bool:
    replied = message.reply_to_message
    if replied is None or message.from_user is None:
        return False
    content = (message.text or message.caption or "").strip()
    replied_text = (replied.text or replied.caption or "").strip()
    return bool(content and replied_text.startswith(VISIT_PROMPT_PREFIX))


def _main_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏳 Моё государство", callback_data="worldhub:mine")
    builder.button(text="🌐 Государства", callback_data="worldhub:states")
    builder.button(text="🤝 Дипломатия", callback_data="worldhub:diplomacy")
    builder.button(text="🎩 Назначить посла", callback_data="worldx:ambassador")
    builder.button(text="🗺 Карта мира", callback_data="worldhub:map")
    builder.button(text="📰 Мировые новости", callback_data="worldhub:news")
    builder.button(text="📜 Хроника", callback_data="worldhub:chronicle")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def _diplomacy_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🤝 Предложить союз", callback_data="worldx:choose:alliance")
    builder.button(text="💔 Разорвать союз", callback_data="worldx:choose:break")
    builder.button(text="⚔️ Объявить войну", callback_data="worldx:choose:war")
    builder.button(text="🕊 Прекратить войну", callback_data="worldx:choose:peace")
    builder.button(text="🛬 Пригласить государство", callback_data="worldx:choose:invite")
    builder.button(text="🖕 Оскорбить государство", callback_data="worldx:choose:insult")
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    builder.adjust(2, 2, 1, 1, 1)
    return builder.as_markup()


def _back_to_diplomacy_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К дипломатии", callback_data="worldhub:diplomacy")
    return builder.as_markup()


def _ambassador_markup(has_ambassador: bool) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_ambassador:
        builder.button(text="🎩 Снять посла", callback_data="worldx:ambassador:remove")
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    builder.adjust(1)
    return builder.as_markup()


def _target_markup(action: str, states) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for state in states[:40]:
        label = " ".join(state.title.split())
        if len(label) > 25:
            label = label[:24].rstrip() + "…"
        builder.button(
            text=f"№{state.world_id} · {label}",
            callback_data=f"worldx:do:{action}:{state.world_id}",
        )
    builder.button(text="⬅️ К дипломатии", callback_data="worldhub:diplomacy")
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


async def _main_text(chat_id: int, title: str) -> str | None:
    service = get_world_service()
    state = await service.get_state(chat_id, title)
    profile = await service.get_profile(chat_id, title)
    if state is None or profile is None:
        return None
    return (
        "🌍 Мир Упупы\n\n"
        f"Вы — государство №{state.world_id} «{state.title}».\n"
        f"Международный авторитет: {calculate_authority(profile)}.\n\n"
        "Куда полезем?"
    )


async def _diplomacy_text(chat_id: int, title: str) -> str | None:
    service = get_world_service()
    profile = await service.get_profile(chat_id, title)
    if profile is None:
        return None
    return format_diplomacy(profile, await _alliance_names(service, profile))


async def _candidate_states(chat_id: int, title: str, action: str):
    service = get_world_service()
    profile = await service.get_profile(chat_id, title)
    if profile is None:
        return ()
    if action == "break":
        return profile.allies
    if action == "peace":
        return profile.wars
    states = await service.list_states(chat_id, title)
    if not states:
        return ()
    if action == "war":
        war_ids = {state.world_id for state in profile.wars}
        return tuple(state for state in states if state.world_id not in war_ids)
    return states


async def _show_ambassador(query: types.CallbackQuery) -> None:
    service = get_world_service()
    state = await service.get_state(query.message.chat.id, query.message.chat.title or "Безымянное государство")
    if state is None:
        await query.answer("Государство не найдено.", show_alert=True)
        return
    details = await service.get_details(state.world_id)
    current = details.ambassador_name if details and details.ambassador_name else "не назначен"
    text = (
        "🎩 Посол государства\n\n"
        f"Сейчас: {current}.\n\n"
        "Чтобы назначить посла, ответь на сообщение нужного человека командой «упупа назначь посла». "
        "Назначать и снимать посла могут администраторы государства."
    )
    await query.message.edit_text(
        text,
        reply_markup=_ambassador_markup(bool(details and details.ambassador_user_id)),
    )
    await query.answer()


async def _require_diplomat(query: types.CallbackQuery) -> bool:
    if await is_chat_admin(bot, query.message.chat.id, query.from_user.id):
        return True
    await query.answer(
        "Это дипломатическое решение доступно администраторам и назначенному послу.",
        show_alert=True,
    )
    return False


async def _generate_insult(chat_id: int, source_title: str, target_title: str) -> str:
    task = f"""Сгенерируй один короткий официальный дипломатический выпад от государства «{source_title}» в адрес государства «{target_title}» для игрового Мира Упупы.
Это должно звучать как абсурдно-серьёзное заявление МИДа: едко, смешно и персонально по названиям государств, но без угроз реального насилия и без оскорблений по защищённым признакам.
Можно использовать мат и стиль текущего чата. 1–2 предложения, без Markdown, без кавычек и без пояснений."""
    prompt = build_prompt_with_current_chat_prompt(
        str(chat_id),
        task,
        task_name="дипломатическое оскорбление Мира Упупы",
    )
    try:
        text = await _generate_with_active_model(prompt, str(chat_id), is_summarization=True)
    except Exception:
        logging.exception("World diplomatic insult generation failed chat_id=%s", chat_id)
        text = ""
    clean = " ".join((text or "").split()).strip()
    if clean:
        return clean[:700]
    return (
        f"МИД государства «{source_title}» сообщает, что государство «{target_title}» "
        "пока выглядит как географическое недоразумение, которому по ошибке выдали внешнюю политику."
    )


async def _do_alliance(query: types.CallbackQuery, target_world_id: int) -> None:
    service = get_world_service()
    result = await service.propose_alliance(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
        target_world_id,
    )
    messages = {
        "unknown_target": "Государства с таким номером не существует.",
        "target_disabled": "Это государство сейчас вне Мира Упупы.",
        "self": "С самим собой союз заключать не надо.",
        "already_allied": "Вы уже союзники.",
        "at_war": "С этим государством идёт война. Сначала её надо прекратить.",
        "duplicate": "Между этими государствами уже висит предложение союза.",
    }
    if result.status in messages:
        await query.answer(messages[result.status], show_alert=True)
        return
    if result.status != "created" or not result.request or not result.source or not result.target:
        await query.answer("Не удалось создать предложение союза.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="🤝 Принять", callback_data=f"world:alliance:{result.request.request_id}:accept")
    builder.button(text="🖕 Отклонить", callback_data=f"world:alliance:{result.request.request_id}:reject")
    builder.adjust(2)
    try:
        await bot.send_message(
            result.target.chat_id,
            "🌍 Предложение союза\n\n"
            f"Государство №{result.source.world_id} — {result.source.title} предлагает заключить союз.\n\n"
            "Решение может принять администратор или посол.",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        logging.exception("World alliance interactive delivery failed target=%s", result.target.world_id)
        await service.cancel_request(result.request.request_id)
        await query.answer("Не удалось доставить предложение. Запрос отменён.", show_alert=True)
        return
    await query.answer("Предложение союза отправлено")
    await query.message.answer(
        f"🤝 Предложение союза отправлено государству №{result.target.world_id} — {result.target.title}."
    )


async def _do_break(query: types.CallbackQuery, target_world_id: int) -> None:
    service = get_world_service()
    result = await service.break_alliance(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
        target_world_id,
    )
    if result.status == "not_allied":
        await query.answer("Между вами и так нет союза.", show_alert=True)
        return
    if result.status != "broken" or not result.source or not result.target:
        await query.answer("Не удалось разорвать союз.", show_alert=True)
        return
    await query.answer("Союз разорван")
    await query.message.answer(f"💔 Союз с государством №{result.target.world_id} разорван.")
    try:
        await bot.send_message(
            result.target.chat_id,
            f"💔 Государство №{result.source.world_id} — {result.source.title} разорвало союз с вами.",
        )
    except Exception:
        logging.exception("World interactive alliance break notification failed target=%s", target_world_id)


async def _do_war(query: types.CallbackQuery, target_world_id: int) -> None:
    service = get_world_service()
    result = await service.declare_war(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
        target_world_id,
    )
    messages = {
        "unknown_target": "Государства с таким номером не существует.",
        "target_disabled": "Это государство сейчас вне Мира Упупы.",
        "self": "Самим себе войну объявлять нельзя.",
        "already_at_war": "Вы уже воюете с этим государством.",
    }
    if result.status in messages:
        await query.answer(messages[result.status], show_alert=True)
        return
    if result.status != "declared" or not result.source or not result.target:
        await query.answer("Не удалось объявить войну.", show_alert=True)
        return
    await query.answer("Война объявлена")
    prefix = "⚔️ Союз расторгнут. " if result.previous_relation == "allied" else "⚔️ "
    await query.message.answer(
        f"{prefix}Государству №{result.target.world_id} — {result.target.title} объявлена война."
    )
    try:
        await bot.send_message(
            result.target.chat_id,
            "⚔️ Сэр, эти пидорасы объявили нам войну!\n\n"
            f"Государство №{result.source.world_id} — {result.source.title}.",
        )
    except Exception:
        logging.exception("World interactive war notification failed target=%s", target_world_id)


async def _do_peace(query: types.CallbackQuery, target_world_id: int) -> None:
    service = get_world_service()
    result = await service.end_war(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
        target_world_id,
    )
    if result.status == "not_at_war":
        await query.answer("Вы с этим государством и так не воюете.", show_alert=True)
        return
    if result.status != "ended" or not result.source or not result.target:
        await query.answer("Не удалось прекратить войну.", show_alert=True)
        return
    await query.answer("Война прекращена")
    await query.message.answer(
        f"🕊 Война с государством №{result.target.world_id} — {result.target.title} прекращена."
    )
    try:
        await bot.send_message(
            result.target.chat_id,
            f"🕊 Государство №{result.source.world_id} — {result.source.title} прекратило войну с вами.",
        )
    except Exception:
        logging.exception("World interactive peace notification failed target=%s", target_world_id)


async def _do_invite(query: types.CallbackQuery, target_world_id: int) -> None:
    service = get_world_service()
    source = await service.get_state(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
    )
    target = await service.get_state_by_world_id(target_world_id)
    if source is None or target is None or not target.enabled:
        await query.answer("Государство сейчас недоступно.", show_alert=True)
        return
    if source.world_id == target.world_id:
        await query.answer("Самих себя в гости звать странно даже по меркам Упупы.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="🛬 Приехать", callback_data=f"worldvisit:{source.world_id}:accept")
    builder.button(text="🙅 Не ехать", callback_data=f"worldvisit:{source.world_id}:reject")
    builder.adjust(2)
    try:
        sent = await bot.send_message(
            target.chat_id,
            "🛬 Государственное приглашение\n\n"
            f"Государство №{source.world_id} — {source.title} приглашает вас с государственным визитом.\n\n"
            "Поедем смотреть, что эти люди считают достопримечательностями?",
            reply_markup=builder.as_markup(),
        )
    except Exception:
        logging.exception("World visit invitation delivery failed target=%s", target.world_id)
        await query.answer("Не удалось доставить приглашение.", show_alert=True)
        return
    await record_interaction_event(
        service,
        "state_visit_invited",
        actor_state=source.world_id,
        target_state=target.world_id,
        payload={"invited_by": query.from_user.full_name},
        dedupe_key=f"visit_invited:{target.world_id}:{sent.message_id}",
    )
    await query.answer("Приглашение отправлено")
    await query.message.answer(
        f"🛬 Государство №{target.world_id} — {target.title} приглашено в гости."
    )


async def _do_insult(query: types.CallbackQuery, target_world_id: int) -> None:
    service = get_world_service()
    source = await service.get_state(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
    )
    target = await service.get_state_by_world_id(target_world_id)
    if source is None or target is None or not target.enabled:
        await query.answer("Государство сейчас недоступно.", show_alert=True)
        return
    if source.world_id == target.world_id:
        await query.answer("Для самооскорбления у нас есть обычный чат.", show_alert=True)
        return
    remaining = await insult_cooldown_remaining(service, source.world_id)
    if remaining is not None:
        minutes = max(1, math.ceil(remaining.total_seconds() / 60))
        await query.answer(
            f"МИД ещё не остыл. Следующее оскорбление примерно через {minutes} мин.",
            show_alert=True,
        )
        return

    await query.answer("МИД подбирает формулировки")
    insult = await _generate_insult(query.message.chat.id, source.title, target.title)
    remaining = await insult_cooldown_remaining(service, source.world_id)
    if remaining is not None:
        return
    await record_interaction_event(
        service,
        "state_insult",
        actor_state=source.world_id,
        target_state=target.world_id,
        payload={"text": insult, "sent_by": query.from_user.full_name},
    )
    await query.message.answer(
        f"🖕 Дипломатический выпад в адрес государства №{target.world_id} — {target.title}:\n\n{insult}"
    )
    try:
        await bot.send_message(
            target.chat_id,
            "🖕 МИД сообщает о дипломатическом выпадении осадков.\n\n"
            f"Государство №{source.world_id} — {source.title} заявило:\n\n{insult}",
        )
    except Exception:
        logging.exception("World insult delivery failed target=%s", target.world_id)


async def _handle_visit_decision(query: types.CallbackQuery, source_world_id: int, decision: str) -> None:
    service = get_world_service()
    target = await service.get_state(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
    )
    source = await service.get_state_by_world_id(source_world_id)
    if target is None or source is None or not source.enabled:
        await query.answer("Одно из государств уже недоступно.", show_alert=True)
        return
    if source.world_id == target.world_id:
        await query.answer("Это приглашение явно попало не туда.", show_alert=True)
        return
    if decision not in {"accept", "reject"}:
        await query.answer("Неизвестный ответ на приглашение.", show_alert=True)
        return

    event_type = "state_visit_accepted" if decision == "accept" else "state_visit_rejected"
    recorded = await record_interaction_event(
        service,
        event_type,
        actor_state=target.world_id,
        target_state=source.world_id,
        payload={"answered_by": query.from_user.full_name},
        dedupe_key=f"visit_decision:{target.world_id}:{query.message.message_id}",
    )
    if service.ledger is not None and not recorded:
        await query.answer("Это приглашение уже обработано.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if decision == "reject":
        await query.answer("Визит отклонён")
        await query.message.answer("🙅 Решено никуда не ехать. Международный туризм отменяется.")
        try:
            await bot.send_message(
                source.chat_id,
                f"🙅 Государство №{target.world_id} — {target.title} отказалось от визита.",
            )
        except Exception:
            logging.exception("World visit rejection notification failed source=%s", source.world_id)
        return

    await query.answer("Едем")
    await query.message.answer(
        f"🛬 Приглашение принято. Делегация отправляется в государство №{source.world_id} — {source.title}."
    )
    try:
        await bot.send_message(
            source.chat_id,
            f"{VISIT_PROMPT_PREFIX}\n\n"
            f"Гости: государство №{target.world_id} — {target.title}.\n"
            "Они приехали смотреть, чем вы тут вообще живёте.\n\n"
            "Любой участник чата может ответить на это сообщение текстом и рассказать, что именно показывает гостям. "
            "Упупа подпишет автора и передаст это делегации.",
        )
    except Exception:
        logging.exception("World visit host prompt failed source=%s", source.world_id)


@router.message(_is_hub_command)
async def interactive_world_hub(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    text = await _main_text(message.chat.id, _title(message))
    if text:
        await message.reply(text, reply_markup=_main_markup())


@router.message(_is_diplomacy_command)
async def interactive_diplomacy(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    text = await _diplomacy_text(message.chat.id, _title(message))
    if text:
        await message.reply(text, reply_markup=_diplomacy_markup())


@router.message(_is_visit_showcase_reply)
async def visit_showcase(message: types.Message):
    service = await _require_world(message)
    if service is None or message.from_user is None or message.reply_to_message is None:
        return
    replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    match = _VISIT_TARGET_RE.search(replied_text)
    if match is None:
        return
    target_world_id = int(match.group(1))
    source = await service.get_state(message.chat.id, _title(message))
    target = await service.get_state_by_world_id(target_world_id)
    if source is None or target is None or not target.enabled:
        await message.reply("🎒 Делегация уже куда-то уехала. Показывать некому.")
        return
    content = " ".join((message.text or message.caption or "").split()).strip()[:1000]
    if not content:
        return
    name = message.from_user.full_name or (
        f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    )
    await record_interaction_event(
        service,
        "state_visit_showcase",
        actor_state=source.world_id,
        target_state=target.world_id,
        payload={
            "user_id": message.from_user.id,
            "user_name": name,
            "text": content,
        },
    )
    await message.reply(f"🎒 {name} показал гостям: {content}")
    try:
        await bot.send_message(
            target.chat_id,
            f"🎒 Экскурсия по государству №{source.world_id} — {source.title}.\n\n"
            f"{name} показал вам: {content}",
        )
    except Exception:
        logging.exception("World visit showcase delivery failed target=%s", target.world_id)


@router.callback_query(
    lambda query: bool(query.data)
    and (
        query.data in {"worldhub:main", "worldhub:diplomacy"}
        or query.data.startswith("worldx:")
        or query.data.startswith("worldvisit:")
    )
)
async def world_interaction_callback(query: types.CallbackQuery):
    if query.message is None or query.from_user is None or not query.data:
        return
    service = get_world_service()
    state = await service.get_state(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
    )
    if state is None or not state.enabled:
        await query.answer("Этот чат сейчас вне Мира Упупы.", show_alert=True)
        return

    if query.data == "worldhub:main":
        text = await _main_text(query.message.chat.id, query.message.chat.title or "Безымянное государство")
        if text:
            await query.message.edit_text(text, reply_markup=_main_markup())
        await query.answer()
        return

    if query.data == "worldhub:diplomacy":
        text = await _diplomacy_text(
            query.message.chat.id,
            query.message.chat.title or "Безымянное государство",
        )
        if text:
            await query.message.edit_text(text, reply_markup=_diplomacy_markup())
        await query.answer()
        return

    parts = query.data.split(":")
    if parts[0] == "worldvisit" and len(parts) == 3:
        try:
            source_world_id = int(parts[1])
        except ValueError:
            await query.answer("Кривое приглашение.", show_alert=True)
            return
        await _handle_visit_decision(query, source_world_id, parts[2])
        return

    if parts[:2] == ["worldx", "ambassador"]:
        if len(parts) == 3 and parts[2] == "remove":
            if not await is_strict_chat_admin(bot, query.message.chat.id, query.from_user.id):
                await query.answer("Снимать посла могут только администраторы государства.", show_alert=True)
                return
            await service.set_ambassador(state.world_id, None, None)
        await _show_ambassador(query)
        return

    if len(parts) == 3 and parts[:2] == ["worldx", "choose"]:
        if not await _require_diplomat(query):
            return
        action = parts[2]
        if action not in {"alliance", "break", "war", "peace", "invite", "insult"}:
            await query.answer("Неизвестное дипломатическое действие.", show_alert=True)
            return
        candidates = await _candidate_states(
            query.message.chat.id,
            query.message.chat.title or "Безымянное государство",
            action,
        )
        labels = {
            "alliance": "Кому предложить союз?",
            "break": "С кем разорвать союз?",
            "war": "Кому объявить войну?",
            "peace": "С кем прекратить войну?",
            "invite": "Кого пригласить с государственным визитом?",
            "insult": "Кого сегодня официально обосрать?",
        }
        if not candidates:
            await query.message.edit_text(
                f"🤝 {labels[action]}\n\nПодходящих государств сейчас нет.",
                reply_markup=_back_to_diplomacy_markup(),
            )
        else:
            await query.message.edit_text(
                f"🤝 {labels[action]}",
                reply_markup=_target_markup(action, candidates),
            )
        await query.answer()
        return

    if len(parts) == 4 and parts[:2] == ["worldx", "do"]:
        if not await _require_diplomat(query):
            return
        action = parts[2]
        try:
            target_world_id = int(parts[3])
        except ValueError:
            await query.answer("Кривой номер государства.", show_alert=True)
            return
        handlers = {
            "alliance": _do_alliance,
            "break": _do_break,
            "war": _do_war,
            "peace": _do_peace,
            "invite": _do_invite,
            "insult": _do_insult,
        }
        handler = handlers.get(action)
        if handler is None:
            await query.answer("Неизвестное дипломатическое действие.", show_alert=True)
            return
        await handler(query, target_world_id)
        return

    await query.answer("Неизвестный дипломатический манёвр.", show_alert=True)
