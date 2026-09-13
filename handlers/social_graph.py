"""Telegram transport for the social graph and long-lived relationship commands."""

from __future__ import annotations

import logging
import re

from aiogram import Router, types
from aiogram.types import BufferedInputFile

from core.settings import BLOCKED_USERS
from features.social_graph.ai import interpret_centrality, interpret_personal_summary
from features.social_graph.analysis import (
    aggregate_edges,
    build_personal_summary,
    rank_central_participants,
    select_render_graph,
)
from features.social_graph.caricature import (
    MAX_CRINGE_EDGES,
    MAX_CRINGE_NODES,
    build_cringe_social_graph_prompt,
)
from features.social_graph.image_generation import generate_social_graph_image
from features.social_graph.interaction_analysis import (
    build_cringe_graph_explanation,
    build_edge_interaction_profiles,
    edge_keywords,
)
from features.social_graph.relationships import (
    RelationshipView,
    get_relationship,
    get_relationship_history,
    get_relationships,
)
from features.social_graph.rendering import render_cringe_graph_png_async, render_graph_png_async
from features.social_graph.service import (
    REPLY_WEIGHT,
    capture_reaction,
    get_graph_data,
    is_social_graph_enabled,
    resolve_relationship_usernames,
)


router = Router(name="social_graph")
CRINGE_GRAPH_CAPTION = "рожи и художественная хуита"
_USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{3,})")


def _is_command(message: types.Message, command: str) -> bool:
    return bool(
        message.text
        and message.from_user
        and message.from_user.id not in BLOCKED_USERS
        and message.text.strip().lower() == command
    )


def _starts_command(message: types.Message, command: str) -> bool:
    if not message.text or not message.from_user or message.from_user.id in BLOCKED_USERS:
        return False
    text = message.text.strip().lower()
    return text == command or text.startswith(command + " ")


def _is_cringe_graph_command(message: types.Message) -> bool:
    return _is_command(message, "соцграф рисунок") or _is_command(message, "соцграф картинка")


def _disabled_text() -> str:
    return "🕸 Соцграф в этом чате отключён. Админ может включить его через «упупа настройки»."


def _format_weight(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _name(names: dict[int, str], user_id: int) -> str:
    return names.get(user_id, "Участник")


def _central_reason(item) -> str:
    if item.betweenness >= 0.2 and item.unique_neighbors >= 3:
        return (
            f"связан с {item.unique_neighbors} участниками и часто лежит на коротких путях "
            "между разными частями графа"
        )
    if item.strong_neighbors >= 3:
        return f"имеет {item.strong_neighbors} сильных связей с разными участниками"
    return (
        f"имеет суммарную силу связей {_format_weight(item.weighted_degree)} "
        f"с {item.unique_neighbors} участниками"
    )


def _pair_name(view: RelationshipView) -> str:
    return f"{view.user_a_name} ↔ {view.user_b_name}"


def _other_name(view: RelationshipView, user_id: int) -> str:
    return view.user_b_name if view.user_a_id == int(user_id) else view.user_a_name


def _relationship_description(view: RelationshipView) -> str:
    if view.reciprocity < 0.35 and view.interaction_count >= 6:
        active = view.user_a_name if view.a_to_b_count > view.b_to_a_count else view.user_b_name
        quiet = view.user_b_name if active == view.user_a_name else view.user_a_name
        return f"{active} заметно чаще инициирует контакт. {quiet} держит социальную оборону."
    if view.affinity >= 65 and view.tension >= 50:
        return "Связь крепкая, а спокойствие в неё, похоже, просто не завезли."
    if view.tension >= 55:
        return "Заметная часть общей истории проходит через подтверждённые конфликтные эпизоды."
    if view.shared_event_count >= 2:
        return "Это уже не просто счётчик reply: пара регулярно попадает в общую историю чата."
    if view.affinity >= 60:
        return "Устойчивая взаимная вовлечённость без необходимости устраивать гражданскую войну."
    return "Связь уже видна, но до отдельного сериала про этих двоих пока далеко."


async def _resolve_relationship_pair(message: types.Message, command: str) -> tuple[int, int] | None:
    text = (message.text or "").strip()
    tail = text[len(command):].strip() if text.lower().startswith(command) else ""
    usernames = _USERNAME_RE.findall(tail)
    if usernames:
        resolved = await resolve_relationship_usernames(message.chat.id, usernames[:2])
        people = []
        for username in usernames[:2]:
            person = resolved.get(username.lower())
            if person is not None:
                people.append(int(person[0]))
        if len(usernames) >= 2:
            if len(people) < 2:
                return None
            return people[0], people[1]
        if people and int(message.from_user.id) != people[0]:
            return int(message.from_user.id), people[0]

    replied = getattr(message, "reply_to_message", None)
    target = getattr(replied, "from_user", None) if replied else None
    if target and not target.is_bot and int(target.id) != int(message.from_user.id):
        return int(message.from_user.id), int(target.id)
    return None


async def _ensure_available(message: types.Message) -> bool:
    if getattr(message.chat.type, "value", message.chat.type) not in {"group", "supergroup"}:
        await message.reply("Соцграф работает только внутри группового чата.")
        return False
    if not is_social_graph_enabled(message.chat.id):
        await message.reply(_disabled_text())
        return False
    return True


@router.message(lambda message: _is_command(message, "соцграф"))
async def handle_social_graph(message: types.Message):
    if not await _ensure_available(message):
        return
    data = await get_graph_data(message.chat.id)
    edges = aggregate_edges(data.interactions)
    if not edges:
        await message.reply(f"За последние {data.period_days} дней связей пока недостаточно для графа.")
        return

    view = select_render_graph(edges, data.names)
    if not view.edges:
        await message.reply(f"За последние {data.period_days} дней связей пока недостаточно для графа.")
        return

    png = await render_graph_png_async(view)
    shown_nodes = len(view.nodes)
    shown_edges = len(view.edges)
    suffix = ""
    if shown_nodes < view.total_node_count or shown_edges < view.total_edge_count:
        suffix = (
            f" Показаны самые значимые {shown_nodes} из {view.total_node_count} участников "
            f"и {shown_edges} из {view.total_edge_count} связей."
        )
    caption = (
        f"🕸 Соцграф за последние {data.period_days} дней. Толщина линии = сила связи; "
        f"стрелка появляется только при заметной асимметрии.{suffix}"
    )
    await message.answer_photo(BufferedInputFile(png, filename="social_graph.png"), caption=caption)


@router.message(lambda message: _is_cringe_graph_command(message))
async def handle_cringe_social_graph(message: types.Message):
    if not await _ensure_available(message):
        return
    data = await get_graph_data(message.chat.id)
    edges = aggregate_edges(data.interactions)
    if not edges:
        await message.reply(f"За последние {data.period_days} дней даже всрать пока нечего — связей мало.")
        return

    view = select_render_graph(
        edges,
        data.names,
        max_nodes=MAX_CRINGE_NODES,
        max_edges=MAX_CRINGE_EDGES,
    )
    if not view.edges:
        await message.reply(f"За последние {data.period_days} дней даже всрать пока нечего — связей мало.")
        return

    await message.reply("🖍 Ща испорчу вашу статистику в Paint.")
    prompt = build_cringe_social_graph_prompt(view, data.period_days)
    portrait_sheet, _provider = await generate_social_graph_image(prompt)
    if not portrait_sheet:
        await message.reply("Не смог всрать картинку: рисовалка сдохла.")
        return

    profiles = build_edge_interaction_profiles(data.interactions, view.edges)

    try:
        image = await render_cringe_graph_png_async(view, portrait_sheet, edge_keywords(profiles))
    except Exception as exc:
        logging.warning("[social_graph] hybrid render failed: %s", exc, exc_info=True)
        await message.reply("Рожи нарисовал, а собрать их в соцграф не смог.")
        return

    await message.answer_photo(
        BufferedInputFile(image, filename="social_graph_cringe.png"),
        caption=CRINGE_GRAPH_CAPTION,
    )
    await message.answer(build_cringe_graph_explanation(view, profiles, data.names))


@router.message(lambda message: _starts_command(message, "отношения") and not _is_command(message, "отношения чата"))
async def handle_relationship(message: types.Message):
    if not await _ensure_available(message):
        return
    pair = await _resolve_relationship_pair(message, "отношения")
    if pair is None:
        await message.reply("Ответь командой «отношения» на сообщение человека или напиши «отношения @user» / «отношения @user1 @user2».")
        return
    view = await get_relationship(message.chat.id, *pair)
    if view is None:
        await message.reply("У этой пары пока недостаточно общей истории: ни устойчивых взаимодействий, ни общего события в Летописи.")
        return

    lines = [
        f"❤️ <b>{_pair_name(view)}</b>",
        "",
        f"Уровень: <b>{view.level}/15</b>",
        f"Тип: <b>{view.archetype}</b>",
        f"Близость: <b>{view.affinity}/100</b>",
        f"Напряжение: <b>{view.tension}/100</b>",
        f"Взаимность: <b>{round(view.reciprocity * 100)}%</b>",
        "",
        _relationship_description(view),
    ]
    if view.shared_events:
        main_event = max(view.shared_events, key=lambda event: event.importance_score)
        lines.extend(("", f"Главный эпизод: <b>«{main_event.title}»</b>"))
    lines.extend(("", f"Тренд: <b>{view.trend}</b>"))
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(lambda message: _is_command(message, "мои отношения"))
async def handle_my_relationships(message: types.Message):
    if not await _ensure_available(message):
        return
    views = await get_relationships(message.chat.id, user_id=message.from_user.id)
    if not views:
        await message.reply("У тебя пока не накопилось отношений, которые можно уверенно показать.")
        return
    lines = ["❤️ <b>Твои главные отношения</b>", ""]
    for index, view in enumerate(views[:6], 1):
        lines.append(
            f"{index}. <b>{_other_name(view, message.from_user.id)}</b> — "
            f"уровень {view.level}, {view.archetype}; {view.trend}."
        )
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(lambda message: _is_command(message, "отношения чата"))
async def handle_chat_relationships(message: types.Message):
    if not await _ensure_available(message):
        return
    views = await get_relationships(message.chat.id)
    if not views:
        await message.reply("У чата пока недостаточно общей истории для рейтинга отношений.")
        return

    closest = max(views, key=lambda item: (item.affinity, item.level))
    strongest = max(views, key=lambda item: (item.level, item.strength))
    conflict = max(views, key=lambda item: item.tension)
    mutual_pool = [item for item in views if item.interaction_count >= 4]
    mutual = max(mutual_pool, key=lambda item: item.reciprocity) if mutual_pool else None
    one_sided = min(mutual_pool, key=lambda item: item.reciprocity) if mutual_pool else None
    rising = max(views, key=lambda item: item.recent_7d - item.previous_21d / 3.0)

    lines = ["🕸 <b>Отношения чата</b>", ""]
    lines.append(f"❤️ Самые близкие: <b>{_pair_name(closest)}</b> — {closest.archetype}")
    if conflict.tension > 0:
        lines.append(f"⚔️ Самые напряжённые: <b>{_pair_name(conflict)}</b> — {conflict.archetype}")
    if mutual is not None:
        lines.append(f"🤝 Самые взаимные: <b>{_pair_name(mutual)}</b> — {round(mutual.reciprocity * 100)}%")
    if one_sided is not None and one_sided.reciprocity < 0.65:
        lines.append(f"🫠 Самая односторонняя связь: <b>{_pair_name(one_sided)}</b>")
    if rising.recent_7d > 0:
        lines.append(f"📈 Быстрее всех оживают: <b>{_pair_name(rising)}</b> — {rising.trend}")
    if strongest is not closest:
        lines.append(f"🏛 Главная институция: <b>{_pair_name(strongest)}</b> — уровень {strongest.level}/15")
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(lambda message: _starts_command(message, "история отношений"))
async def handle_relationship_history(message: types.Message):
    if not await _ensure_available(message):
        return
    pair = await _resolve_relationship_pair(message, "история отношений")
    if pair is None:
        await message.reply("Ответь «история отношений» на сообщение человека или укажи @user / двух @user.")
        return
    view, snapshots = await get_relationship_history(message.chat.id, *pair)
    if view is None:
        await message.reply("Истории этой пары пока нет.")
        return

    lines = [f"📜 <b>История отношений: {_pair_name(view)}</b>", ""]
    events = sorted(view.shared_events, key=lambda event: event.event_started_at, reverse=True)[:8]
    if events:
        for event in events:
            lines.append(f"• {event.event_started_at:%d.%m.%Y} — <b>{event.title}</b>")
    else:
        lines.append("Пока без отдельных легендарных эпизодов в Летописи.")

    if len(snapshots) >= 2:
        latest = snapshots[0]
        oldest = snapshots[-1]
        if latest.level != oldest.level or latest.archetype != oldest.archetype:
            lines.extend((
                "",
                f"Динамика снимков: уровень {oldest.level} → {latest.level}; "
                f"{oldest.archetype} → {latest.archetype}.",
            ))
    lines.extend(("", f"Сейчас: <b>{view.archetype}</b>, уровень <b>{view.level}/15</b>, {view.trend}."))
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(lambda message: _is_command(message, "мои связи"))
async def handle_my_connections(message: types.Message):
    if not await _ensure_available(message):
        return
    data = await get_graph_data(message.chat.id)
    edges = aggregate_edges(data.interactions)
    summary = build_personal_summary(message.from_user.id, edges)
    if summary.distinct_connections == 0:
        await message.reply(f"За последние {data.period_days} дней у тебя пока нет зафиксированных связей.")
        return

    lines = [f"🕸 Твои связи за последние {data.period_days} дней"]
    if summary.top_outgoing:
        lines.append("\nЧаще взаимодействуешь с:")
        for item in summary.top_outgoing:
            if item.outgoing > 0:
                lines.append(f"• {_name(data.names, item.user_id)} — {_format_weight(item.outgoing)}")
    if summary.top_incoming:
        lines.append("\nЧаще взаимодействуют с тобой:")
        for item in summary.top_incoming:
            if item.incoming > 0:
                lines.append(f"• {_name(data.names, item.user_id)} — {_format_weight(item.incoming)}")
    if summary.strongest_mutual:
        lines.append("\nСамые сильные двусторонние связи:")
        for item in summary.strongest_mutual:
            lines.append(
                f"• {_name(data.names, item.user_id)} — "
                f"ты → {_format_weight(item.outgoing)}, тебе → {_format_weight(item.incoming)}"
            )
    if summary.strongest_asymmetry:
        item = summary.strongest_asymmetry
        if item.outgoing > item.incoming:
            direction = "ты заметно чаще обращаешься к нему/ней"
        else:
            direction = "он/она заметно чаще обращается к тебе"
        lines.append(f"\nАсимметрия: {_name(data.names, item.user_id)} — {direction}.")

    ai_text = await interpret_personal_summary(summary, data.names, str(message.chat.id))
    if ai_text:
        lines.append(f"\nAI: {ai_text}")
    await message.reply("\n".join(lines))


@router.message(lambda message: _is_command(message, "центровой"))
async def handle_central_participant(message: types.Message):
    if not await _ensure_available(message):
        return
    data = await get_graph_data(message.chat.id)
    edges = aggregate_edges(data.interactions)
    ranking = rank_central_participants(edges, strong_edge_threshold=REPLY_WEIGHT)
    if not ranking:
        await message.reply(f"За последние {data.period_days} дней данных для центральности пока недостаточно.")
        return

    lines = [
        f"🕸 Центровые за последние {data.period_days} дней",
        "Считаю не сообщения, а структуру связей: 55% сила связей, 30% betweenness, 15% число разных связей.",
        "",
    ]
    for index, item in enumerate(ranking, 1):
        lines.append(f"{index}. {_name(data.names, item.user_id)} — {_central_reason(item)}.")

    top = ranking[0]
    ai_text = await interpret_centrality(top, _name(data.names, top.user_id), str(message.chat.id))
    if ai_text:
        lines.append(f"\nAI: {ai_text}")
    await message.reply("\n".join(lines))


@router.message_reaction()
async def handle_message_reaction(update: types.MessageReactionUpdated):
    await capture_reaction(update)
