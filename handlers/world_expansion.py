"""Telegram transport for sanctions and the International Court (text commands, summons)."""

from __future__ import annotations

from aiogram import F, Router, types

from core.loader import bot
from core.upupa_utils import normalize_upupa_command
from features.world.delivery import deliver_text
from features.world.expansion import (
    SUMMONS_PREFIX,
    add_defense,
    deliberate,
    file_claim,
    format_international_cases,
    format_sanctions,
    get_case,
    get_case_by_summons,
    impose_sanctions,
    lift_sanctions,
    list_international_cases,
    list_state_sanctions,
    send_summons,
)
from features.world.hub_ui import build_world_main_markup, sanctions_markup
from features.world.presentation import human_until
from handlers.world_common import (
    answer_quietly,
    callback_state,
    is_diplomat,
    replied_bot_text,
    require_diplomat,
    require_world,
    single_flight,
    user_label,
)


router = Router(name="world_expansion")


def _normalized(message: types.Message) -> str:
    return normalize_upupa_command(message.text or "")


def _main_markup() -> types.InlineKeyboardMarkup:
    return build_world_main_markup()


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


def _raw_tail(text: str, words: int) -> str:
    """Original-case remainder after the first ``words`` words (keeps the claim as written)."""
    parts = (text or "").strip().split(maxsplit=words)
    return parts[words].strip() if len(parts) > words else ""


async def _state_names(service) -> dict[int, str]:
    return {state.world_id: state.title for state in await service.list_all_states()}


@router.message(lambda message: bool(message.text) and _normalized(message) in {"санкции", "упупа санкции"})
async def sanctions_status(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    service, state = resolved
    sanctions = await list_state_sanctions(state.world_id, service)
    await message.reply(
        format_sanctions(sanctions, state.world_id, await _state_names(service)),
        reply_markup=sanctions_markup(),
    )


@router.message(lambda message: bool(message.text) and _normalized(message).startswith("упупа санкции "))
async def sanctions_impose(message: types.Message):
    resolved = await require_world(message)
    if resolved is None or message.from_user is None:
        return
    service, state = resolved
    if not await is_diplomat(message.chat.id, message.from_user.id):
        await message.reply("🚫 Санкции могут вводить только администраторы или посол.")
        return
    target_id, normalized_reason = _parse_target_tail(_normalized(message), "упупа санкции")
    if target_id is None:
        await message.reply("Формат: упупа санкции <номер государства> [причина]")
        return
    reason = _raw_tail(message.text or "", 3) or normalized_reason
    status, sanction = await impose_sanctions(state.world_id, target_id, reason, service)
    replies = {
        "self": "🚫 Самосанкции — это уже внутренняя экономическая политика.",
        "unknown_target": "🚫 Такого активного государства нет.",
        "exists": "🚫 Эти санкции уже действуют. Министерство повторяется.",
        "cooldown": "🚫 Министерство санкций перегрелось: подождите несколько минут.",
    }
    if status in replies or sanction is None:
        await message.reply(replies.get(status, "🚫 Не удалось ввести санкции."))
        return
    reason_text = f" Причина: {sanction.reason}" if sanction.reason else ""
    await message.reply(f"🚫 Государство №{target_id} попало под санкции на неделю.{reason_text}")
    target = await service.get_state_by_world_id(target_id)
    await deliver_text(
        bot,
        service,
        target,
        f"🚫 Государство №{state.world_id} — {state.title} ввело против вас санкции на неделю.{reason_text}\n"
        "Пока они действуют, вы не можете звать его в гости, оскорблять, дарить и предлагать союз.",
    )


@router.message(lambda message: bool(message.text) and _normalized(message).startswith("упупа снять санкции "))
async def sanctions_lift(message: types.Message):
    resolved = await require_world(message)
    if resolved is None or message.from_user is None:
        return
    service, state = resolved
    if not await is_diplomat(message.chat.id, message.from_user.id):
        await message.reply("🚫 Снимать санкции могут только администраторы или посол.")
        return
    target_id, _reason = _parse_target_tail(_normalized(message), "упупа снять санкции")
    if target_id is None:
        await message.reply("Формат: упупа снять санкции <номер государства>")
        return
    status = await lift_sanctions(state.world_id, target_id, service)
    if status != "lifted":
        await message.reply("Активных санкций от нас против этого государства не найдено.")
        return
    await message.reply(f"🕊 Санкции против государства №{target_id} сняты. Таможня выдохнула.")
    await deliver_text(
        bot,
        service,
        await service.get_state_by_world_id(target_id),
        f"🕊 Государство №{state.world_id} сняло с вас санкции.",
    )


@router.message(
    lambda message: bool(message.text)
    and _normalized(message) in {"международный суд", "упупа международный суд"}
)
async def international_practice(message: types.Message):
    resolved = await require_world(message)
    if resolved is None:
        return
    service, _state = resolved
    cases = await list_international_cases(service=service)
    await message.reply(format_international_cases(cases, await _state_names(service)))


@router.message(
    lambda message: bool(message.text)
    and _normalized(message).startswith("упупа международный суд ")
)
async def international_case(message: types.Message):
    resolved = await require_world(message)
    if resolved is None or message.from_user is None:
        return
    service, state = resolved
    if not await is_diplomat(message.chat.id, message.from_user.id):
        await message.reply("⚖️ Международные иски подают только администраторы или посол.")
        return
    target_id, normalized_claim = _parse_target_tail(_normalized(message), "упупа международный суд")
    claim = _raw_tail(message.text or "", 4) or normalized_claim
    if target_id is None or not claim:
        await message.reply("Формат: упупа международный суд <номер государства> <претензия>")
        return
    status, case, remaining = await file_claim(service, state.world_id, target_id, claim)
    replies = {
        "unknown_target": "⚖️ Ответчик не найден в активном Мире Упупы.",
        "self": "⚖️ Судиться с самим собой можно, но это называется внутренний чат.",
        "empty": "⚖️ Нужна хотя бы формулировка претензии.",
        "pending_exists": "⚖️ Между вами уже идёт дело. Дождитесь решения.",
    }
    if status == "cooldown" and remaining is not None:
        await message.reply(f"⚖️ Новый иск можно подать через {human_until_delta(remaining)}. Суд не резиновый.")
        return
    if status in replies or case is None:
        await message.reply(replies.get(status, "⚖️ Суд не смог принять иск."))
        return
    delivered = await send_summons(bot, service, case)
    if not delivered:
        await message.reply("⚖️ Ответчик не получил повестку, суд рассматривает дело без защиты…")
        await deliberate(bot, service, case.case_id)
        return
    await message.reply(
        f"⚖️ Иск зарегистрирован, дело №{case.case_id}. Ответчик получил повестку и час на защиту, "
        "после чего суд вынесет решение и пришлёт его обеим сторонам."
    )


def human_until_delta(delta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    hours, rest = divmod(seconds, 3600)
    return f"{hours} ч {rest // 60} мин" if hours else f"{max(1, rest // 60)} мин"


def _is_defense_reply(message: types.Message) -> bool:
    return replied_bot_text(message).startswith(SUMMONS_PREFIX)


@router.message(_is_defense_reply)
async def defense_reply(message: types.Message):
    resolved = await require_world(message)
    if resolved is None or message.from_user is None or message.reply_to_message is None:
        return
    service, state = resolved
    case = await get_case_by_summons(service, state.world_id, message.reply_to_message.message_id)
    if case is None:
        await message.reply("⚖️ Не нашёл это дело в судебном реестре.")
        return
    if not case.pending:
        await message.reply("⚖️ Суд уже удалился в совещательную комнату. Поздно.")
        return
    statement = " ".join((message.text or message.caption or "").split())
    if not statement:
        return
    if await add_defense(service, case.case_id, user_label(message.from_user), statement):
        deadline = f" До решения: {human_until(case.defense_deadline)}." if case.defense_deadline else ""
        await message.reply(f"⚖️ Довод защиты приобщён к делу №{case.case_id}.{deadline}")
    else:
        await message.reply("⚖️ Защита уже исписала все тома дела. Больше не влезет.")


@router.callback_query(F.data.startswith("worldcourt:now:"))
async def court_now_callback(query: types.CallbackQuery):
    resolved = await callback_state(query)
    if resolved is None:
        return
    service, state = resolved
    if not await require_diplomat(query):
        return
    try:
        case_id = int((query.data or "").rsplit(":", 1)[1])
    except ValueError:
        await query.answer("Кривое дело.", show_alert=True)
        return
    case = await get_case(service, case_id)
    if case is None or case.defendant_state != state.world_id:
        await query.answer("Это дело вас не касается.", show_alert=True)
        return
    if not case.pending:
        await query.answer("Решение уже вынесено или выносится.", show_alert=True)
        return
    async with single_flight(f"court:{case_id}") as acquired:
        if not acquired:
            await query.answer("Суд уже заседает.")
            return
        await answer_quietly(query, "Суд удаляется в совещательную комнату")
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await deliberate(bot, service, case_id)
