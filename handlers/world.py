"""Telegram transport for World of Upupa commands and diplomacy callbacks."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.loader import bot
from core.upupa_utils import normalize_upupa_command
from features.world.permissions import is_chat_admin
from features.world.service import (
    format_diplomacy,
    format_states,
    format_world_profile,
    get_world_service,
)


router = Router(name="world")


def _title(message: types.Message) -> str:
    return message.chat.title or "Безымянное государство"


def _is_group(message: types.Message) -> bool:
    return getattr(message.chat, "type", None) in {"group", "supergroup"}


def _target_world_id(text: str, prefix: str) -> int | None:
    raw_target = normalize_upupa_command(text).removeprefix(prefix).strip().lstrip("№#")
    try:
        return int(raw_target)
    except ValueError:
        return None


async def _require_world(message: types.Message):
    if not _is_group(message):
        await message.reply("🌍 Мир Упупы работает только в групповых чатах.")
        return None
    service = get_world_service()
    state = await service.get_state(message.chat.id, _title(message))
    if state is None or not state.enabled:
        await message.reply(
            "🌍 Этот чат пока не участвует в Мире Упупы. "
            "Админ может включить его через «упупа настройки»."
        )
        return None
    return service


async def _require_admin(message: types.Message, action: str) -> bool:
    if message.from_user is None or not await is_chat_admin(
        bot,
        message.chat.id,
        message.from_user.id,
    ):
        await message.reply(f"{action} могут только администраторы этого чата.")
        return False
    return True


@router.message(lambda message: message.text and message.text.lower().strip() == "государство")
async def state_profile(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    profile = await service.get_profile(message.chat.id, _title(message))
    if profile is None:
        await message.reply("🌍 Государство выключено.")
        return

    population = None
    try:
        population = await bot.get_chat_member_count(message.chat.id)
    except Exception as exc:
        logging.warning("World population lookup failed chat_id=%s: %s", message.chat.id, exc)
    await message.reply(format_world_profile(profile, population))


@router.message(lambda message: message.text and message.text.lower().strip() == "государства")
async def states_list(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    states = await service.list_states(message.chat.id, _title(message))
    if states is None:
        await message.reply("🌍 Государство выключено.")
        return
    await message.reply(format_states(states))


@router.message(lambda message: message.text and message.text.lower().strip() == "дипломатия")
async def diplomacy_status(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return
    profile = await service.get_profile(message.chat.id, _title(message))
    if profile is None:
        await message.reply("🌍 Государство выключено.")
        return
    await message.reply(format_diplomacy(profile))


@router.message(
    lambda message: message.text
    and normalize_upupa_command(message.text).startswith("упупа предложи союз")
)
async def propose_alliance(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return

    target_world_id = _target_world_id(message.text or "", "упупа предложи союз")
    if target_world_id is None:
        await message.reply("Формат: упупа предложи союз <номер государства>")
        return

    result = await service.propose_alliance(message.chat.id, _title(message), target_world_id)
    if result.status == "unknown_target":
        await message.reply("Государства с таким номером не существует.")
        return
    if result.status == "target_disabled":
        await message.reply("Это государство сейчас не участвует в Мире Упупы.")
        return
    if result.status == "self":
        await message.reply("С самим собой союз заключать не надо. Вы и так вместе.")
        return
    if result.status == "already_allied":
        await message.reply("Вы уже союзники.")
        return
    if result.status == "at_war":
        await message.reply("С этим государством идёт война. Сначала её надо прекратить.")
        return
    if result.status == "duplicate":
        await message.reply("Между этими государствами уже висит предложение союза.")
        return
    if result.status != "created" or not result.request or not result.source or not result.target:
        await message.reply("Не удалось создать дипломатическое предложение.")
        return

    builder = InlineKeyboardBuilder()
    builder.button(
        text="🤝 Принять",
        callback_data=f"world:alliance:{result.request.request_id}:accept",
    )
    builder.button(
        text="🖕 Отклонить",
        callback_data=f"world:alliance:{result.request.request_id}:reject",
    )
    builder.adjust(2)

    diplomatic_note = (
        "🌍 Дипломатическая нота\n\n"
        f"Государство №{result.source.world_id} — {result.source.title}\n"
        "предлагает заключить союз.\n\n"
        "Решение может принять только администратор этого чата."
    )
    try:
        await bot.send_message(
            result.target.chat_id,
            diplomatic_note,
            reply_markup=builder.as_markup(),
        )
    except Exception as exc:
        logging.warning(
            "World alliance delivery failed request_id=%s target_state=%s: %s",
            result.request.request_id,
            result.target.world_id,
            exc,
        )
        await service.cancel_request(result.request.request_id)
        await message.reply(
            "Не удалось доставить предложение. Запрос отменён, дипломатия не пострадала."
        )
        return

    await message.reply(
        f"🤝 Предложение союза отправлено государству №{result.target.world_id} — {result.target.title}."
    )


@router.message(
    lambda message: message.text
    and normalize_upupa_command(message.text).startswith("упупа разорви союз")
)
async def break_alliance(message: types.Message):
    service = await _require_world(message)
    if service is None or not await _require_admin(message, "Разрывать союзы"):
        return

    target_world_id = _target_world_id(message.text or "", "упупа разорви союз")
    if target_world_id is None:
        await message.reply("Формат: упупа разорви союз <номер государства>")
        return

    result = await service.break_alliance(message.chat.id, _title(message), target_world_id)
    if result.status == "unknown_target":
        await message.reply("Государства с таким номером не существует.")
        return
    if result.status == "self":
        await message.reply("Это ваше собственное государство.")
        return
    if result.status == "not_allied":
        await message.reply("Между вами и так нет союза.")
        return
    if result.status != "broken" or not result.source or not result.target:
        await message.reply("Не удалось разорвать союз.")
        return

    await message.reply(f"💔 Союз с государством №{result.target.world_id} разорван.")
    if result.target.enabled:
        try:
            await bot.send_message(
                result.target.chat_id,
                f"💔 Государство №{result.source.world_id} — {result.source.title} разорвало союз с вами.",
            )
        except Exception as exc:
            logging.warning(
                "World alliance break notification failed target_state=%s: %s",
                result.target.world_id,
                exc,
            )


@router.message(
    lambda message: message.text
    and normalize_upupa_command(message.text).startswith("упупа объяви войну")
)
async def declare_war(message: types.Message):
    service = await _require_world(message)
    if service is None or not await _require_admin(message, "Объявлять войну"):
        return

    target_world_id = _target_world_id(message.text or "", "упупа объяви войну")
    if target_world_id is None:
        await message.reply("Формат: упупа объяви войну <номер государства>")
        return

    result = await service.declare_war(message.chat.id, _title(message), target_world_id)
    if result.status == "unknown_target":
        await message.reply("Государства с таким номером не существует.")
        return
    if result.status == "target_disabled":
        await message.reply("Нельзя объявить войну государству, которое сейчас вне Мира Упупы.")
        return
    if result.status == "self":
        await message.reply("Объявить войну самим себе нельзя. Даже здесь есть предел долбоебизма.")
        return
    if result.status == "already_at_war":
        await message.reply("Вы с этим государством уже воюете.")
        return
    if result.status != "declared" or not result.source or not result.target:
        await message.reply("Не удалось объявить войну.")
        return

    if result.previous_relation == "allied":
        await message.reply(
            f"⚔️ Союз расторгнут. Государству №{result.target.world_id} — {result.target.title} объявлена война."
        )
    else:
        await message.reply(
            f"⚔️ Государству №{result.target.world_id} — {result.target.title} объявлена война."
        )

    try:
        await bot.send_message(
            result.target.chat_id,
            "⚔️ ВОЙНА\n\n"
            f"Государство №{result.source.world_id} — {result.source.title}\n"
            "объявило вам войну.",
        )
    except Exception as exc:
        logging.warning(
            "World war declaration notification failed target_state=%s: %s",
            result.target.world_id,
            exc,
        )


@router.message(
    lambda message: message.text
    and normalize_upupa_command(message.text).startswith("упупа прекрати войну")
)
async def end_war(message: types.Message):
    service = await _require_world(message)
    if service is None or not await _require_admin(message, "Прекращать войну"):
        return

    target_world_id = _target_world_id(message.text or "", "упупа прекрати войну")
    if target_world_id is None:
        await message.reply("Формат: упупа прекрати войну <номер государства>")
        return

    result = await service.end_war(message.chat.id, _title(message), target_world_id)
    if result.status == "unknown_target":
        await message.reply("Государства с таким номером не существует.")
        return
    if result.status == "self":
        await message.reply("Это ваше собственное государство.")
        return
    if result.status == "not_at_war":
        await message.reply("Вы с этим государством и так не воюете.")
        return
    if result.status != "ended" or not result.source or not result.target:
        await message.reply("Не удалось прекратить войну.")
        return

    await message.reply(
        f"🕊 Война с государством №{result.target.world_id} — {result.target.title} прекращена. Теперь вы нейтральны."
    )
    if result.target.enabled:
        try:
            await bot.send_message(
                result.target.chat_id,
                f"🕊 Государство №{result.source.world_id} — {result.source.title} прекратило войну с вами. Теперь отношения нейтральные.",
            )
        except Exception as exc:
            logging.warning(
                "World war end notification failed target_state=%s: %s",
                result.target.world_id,
                exc,
            )


@router.callback_query(F.data.startswith("world:alliance:"))
async def alliance_request_callback(query: types.CallbackQuery):
    if query.message is None or query.from_user is None:
        await query.answer("Некорректный дипломатический запрос.", show_alert=True)
        return
    if not await is_chat_admin(bot, query.message.chat.id, query.from_user.id):
        await query.answer(
            "Решать дипломатические вопросы могут только администраторы этого чата.",
            show_alert=True,
        )
        return

    try:
        _, _, request_id_raw, action = (query.data or "").split(":", 3)
        request_id = int(request_id_raw)
    except (TypeError, ValueError):
        await query.answer("Некорректный дипломатический запрос.", show_alert=True)
        return
    if action not in {"accept", "reject"}:
        await query.answer("Неизвестное дипломатическое решение.", show_alert=True)
        return

    service = get_world_service()
    decision = "accepted" if action == "accept" else "rejected"
    result = await service.resolve_request(request_id, query.message.chat.id, decision)

    if result.status == "wrong_target":
        await query.answer("Этот запрос адресован другому государству.", show_alert=True)
        return
    if result.status == "not_found":
        await query.answer("Запрос уже исчез из дипломатических архивов.", show_alert=True)
        return
    if result.status == "already_resolved":
        status = result.request.status if result.request else "обработан"
        await query.answer(f"Запрос уже обработан: {status}.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    if result.status == "disabled":
        await query.answer("Одно из государств уже выключило участие в Мире.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    if result.status == "at_war":
        await query.answer("Между государствами уже идёт война.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if result.status == "accepted" and result.source and result.target:
        text = (
            "🤝 Союз заключён.\n\n"
            f"Государство №{result.source.world_id} — {result.source.title}\n"
            f"и государство №{result.target.world_id} — {result.target.title}\n"
            "теперь союзники."
        )
        try:
            await query.message.edit_text(text, reply_markup=None)
        except Exception as exc:
            logging.warning("Failed to update accepted diplomacy message: %s", exc)
        await query.answer("Союз заключён")
        try:
            await bot.send_message(
                result.source.chat_id,
                f"🤝 Государство №{result.target.world_id} — {result.target.title} приняло ваше предложение союза.",
            )
        except Exception as exc:
            logging.warning(
                "World alliance acceptance notification failed source_state=%s: %s",
                result.source.world_id,
                exc,
            )
        return

    if result.status == "rejected" and result.source and result.target:
        try:
            await query.message.edit_text(
                f"🖕 Предложение союза от государства №{result.source.world_id} отклонено.",
                reply_markup=None,
            )
        except Exception as exc:
            logging.warning("Failed to update rejected diplomacy message: %s", exc)
        await query.answer("Предложение отклонено")
        if result.source.enabled:
            try:
                await bot.send_message(
                    result.source.chat_id,
                    f"🖕 Государство №{result.target.world_id} — {result.target.title} отклонило ваше предложение союза.",
                )
            except Exception as exc:
                logging.warning(
                    "World alliance rejection notification failed source_state=%s: %s",
                    result.source.world_id,
                    exc,
                )
        return

    await query.answer("Не удалось обработать дипломатический запрос.", show_alert=True)
