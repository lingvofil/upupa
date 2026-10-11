"""Telegram transport for the social graph and long-lived relationship commands."""

from __future__ import annotations

from html import escape
import logging
import re

from aiogram import Router, types
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings import BLOCKED_USERS
from infrastructure.ai.execution import ai_feature
from features.social_graph.ai import (
    interpret_centrality,
    interpret_personal_summary,
    interpret_relationship,
    narrate_relationship_history,
)
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
from features.social_graph.cards import PairCardData, fetch_avatar, render_pair_card_async
from features.social_graph.dynamics import SPARK_WEEKS, RelationshipDynamics
from features.social_graph.relationship_history import (
    RelationshipTimelineItem,
    get_integrated_relationship_history,
    load_relationship_dynamics,
)
from features.social_graph.relationships import (
    RelationshipView,
    get_relationship,
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
# Хвост парной команды: пусто, «@user» или «@user1 @user2». Любой другой текст —
# это обычная реплика вроде «история такая, короче…», а не команда.
_PAIR_TAIL_RE = re.compile(r"(?:@[a-z0-9_]{3,}(?:\s+@[a-z0-9_]{3,})?)?")
_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


CALLBACK_PREFIX = "sg:"


def map_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🏴 Фракции", callback_data=f"{CALLBACK_PREFIX}fac")
    builder.button(text="🎭 Роли", callback_data=f"{CALLBACK_PREFIX}roles")
    builder.button(text="🏆 Топ пар", callback_data=f"{CALLBACK_PREFIX}top")
    builder.button(text="📜 Летопись", callback_data=f"{CALLBACK_PREFIX}chr")
    builder.button(text="🖍 Кринж-версия", callback_data=f"{CALLBACK_PREFIX}cringe")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _button_name(name: str) -> str:
    name = " ".join(str(name or "Участник").split())
    return name if len(name) <= 18 else name[:17] + "…"


def relationship_keyboard(view: RelationshipView, *, include_history: bool = True):
    user_a_id, user_b_id = view.user_a_id, view.user_b_id
    builder = InlineKeyboardBuilder()
    if include_history:
        builder.button(text="📖 История", callback_data=f"{CALLBACK_PREFIX}hist:{int(user_a_id)}:{int(user_b_id)}")
    builder.button(text="🎬 Эпизоды", callback_data=f"{CALLBACK_PREFIX}ep:{int(user_a_id)}:{int(user_b_id)}")
    builder.button(text=f"🗂 {_button_name(view.user_a_name)}", callback_data=f"{CALLBACK_PREFIX}dos:{int(user_a_id)}")
    builder.button(text=f"🗂 {_button_name(view.user_b_name)}", callback_data=f"{CALLBACK_PREFIX}dos:{int(user_b_id)}")
    builder.adjust(2 if include_history else 1, 2)
    return builder.as_markup()


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


def _is_pair_command(message: types.Message, *commands: str) -> bool:
    """Match «команда», «команда @user» and «команда @user1 @user2» only."""
    if not message.text or not message.from_user or message.from_user.id in BLOCKED_USERS:
        return False
    text = " ".join(message.text.strip().lower().split())
    for command in commands:
        if text == command:
            return True
        if text.startswith(command + " ") and _PAIR_TAIL_RE.fullmatch(text[len(command):].strip()):
            return True
    return False


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


def _scale_label(value: int) -> str:
    if value < 20:
        return "низкая"
    if value < 45:
        return "умеренная"
    if value < 70:
        return "высокая"
    return "очень высокая"


def _reciprocity_label(value: float) -> str:
    if value < 0.25:
        return "почти односторонняя"
    if value < 0.55:
        return "неровная"
    if value < 0.80:
        return "высокая"
    return "почти зеркальная"


def _telegram_message_link(chat_id: int, message_id: int | None) -> str | None:
    if message_id is None:
        return None
    raw = str(int(chat_id))
    if not raw.startswith("-100"):
        return None
    return f"https://t.me/c/{raw[4:]}/{int(message_id)}"


def _chronicle_event_html(view: RelationshipView) -> str | None:
    if not view.shared_events:
        return None
    event = max(view.shared_events, key=lambda item: item.importance_score)
    title = escape(event.title)
    message_id = event.anchor_message_id or (event.source_message_ids[0] if event.source_message_ids else None)
    link = _telegram_message_link(view.chat_id, message_id)
    if link:
        title = f'<a href="{link}">«{title}»</a>'
    else:
        title = f"«{title}»"
    return f"Главный исторический эпизод: <b>{title}</b>"


def _dynamics_line(dynamics: RelationshipDynamics | None) -> str | None:
    if dynamics is None or dynamics.total_events <= 0:
        return None
    return (
        f"Динамика за {SPARK_WEEKS} нед.: <code>{escape(dynamics.sparkline)}</code> — "
        f"<b>{escape(dynamics.phase)}</b>"
    )


def _reactions_line(view: RelationshipView) -> str | None:
    positive = view.positive_reaction_count
    negative = view.negative_reaction_count
    if positive + negative <= 0:
        return None
    return f"Реакции друг на друга: тёплых <b>{positive}</b>, ядовитых <b>{negative}</b>."


def _history_command(message: types.Message) -> str:
    if _starts_command(message, "история отношений"):
        return "история отношений"
    return "история"


def _month_heading(item: RelationshipTimelineItem) -> str:
    month = _MONTHS[item.timestamp.month - 1].capitalize()
    return f"{month} {item.timestamp.year}"


def _timeline_html(items: tuple[RelationshipTimelineItem, ...]) -> list[str]:
    lines: list[str] = []
    current_month = None
    for item in items:
        month = (item.timestamp.year, item.timestamp.month)
        if month != current_month:
            if lines:
                lines.append("")
            lines.append(f"<b>{_month_heading(item)}</b>")
            current_month = month
        title = escape(item.title)
        if item.chronicle_event is not None:
            event = item.chronicle_event
            message_id = event.anchor_message_id or (event.source_message_ids[0] if event.source_message_ids else None)
            link = _telegram_message_link(event.chat_id, message_id)
            if link:
                title = f'<a href="{link}">{title}</a>'
        lines.append(f"• <b>{item.timestamp.day} — {title}</b>")
        lines.append(escape(item.summary))
    return lines


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
    await send_social_graph(message, message.chat.id)


async def send_social_graph(message: types.Message, chat_id: int) -> None:
    data = await get_graph_data(chat_id)
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
    await message.answer_photo(
        BufferedInputFile(png, filename="social_graph.png"),
        caption=caption,
        reply_markup=map_keyboard(),
    )


@router.message(lambda message: _is_cringe_graph_command(message))
@ai_feature("соцграф картинка")
async def handle_cringe_social_graph(message: types.Message):
    if not await _ensure_available(message):
        return
    await send_cringe_graph(message, message.chat.id)


@ai_feature("соцграф картинка", only_if_unset=True)
async def send_cringe_graph(message: types.Message, chat_id: int) -> None:
    data = await get_graph_data(chat_id)
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


@router.message(lambda message: _is_pair_command(message, "отношения"))
@ai_feature("отношения")
async def handle_relationship(message: types.Message):
    if not await _ensure_available(message):
        return
    pair = await _resolve_relationship_pair(message, "отношения")
    if pair is None:
        await message.reply("Ответь командой «отношения» на сообщение человека или напиши «отношения @user» / «отношения @user1 @user2».")
        return
    await send_relationship_card(message, message.chat.id, *pair)


@ai_feature("отношения", only_if_unset=True)
async def send_relationship_card(message: types.Message, chat_id: int, user_a_id: int, user_b_id: int) -> None:
    view = await get_relationship(chat_id, user_a_id, user_b_id)
    if view is None:
        await message.reply("У этой пары пока недостаточно общей истории: ни устойчивых взаимодействий, ни общего события в Летописи.")
        return

    comment = await interpret_relationship(view, str(chat_id)) or _relationship_description(view)
    dynamics = await load_relationship_dynamics(chat_id, view.user_a_id, view.user_b_id)
    lines = [
        f"❤️ <b>{escape(_pair_name(view))}</b>",
        "",
        f"Уровень <b>{view.level}/15</b> — «<b>{escape(view.archetype)}</b>».",
        (
            f"Близость {_scale_label(view.affinity)} (<b>{view.affinity}/100</b>), "
            f"напряжение {_scale_label(view.tension)} (<b>{view.tension}/100</b>), "
            f"взаимность {_reciprocity_label(view.reciprocity)} (<b>{round(view.reciprocity * 100)}%</b>)."
        ),
        f"Сейчас: <b>{escape(view.trend)}</b>.",
    ]
    for extra in (_dynamics_line(dynamics), _reactions_line(view)):
        if extra:
            lines.append(extra)
    lines.extend(("", escape(comment)))
    main_event = _chronicle_event_html(view)
    if main_event:
        lines.extend(("", main_event))

    if await _send_pair_card_photo(message, view, dynamics, comment, main_event):
        return
    await message.reply(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=relationship_keyboard(view),
    )


CAPTION_LIMIT = 1024


async def _send_pair_card_photo(
    message: types.Message,
    view: RelationshipView,
    dynamics: RelationshipDynamics | None,
    comment: str,
    main_event_html: str | None,
) -> bool:
    """Card image with the numbers; the caption keeps the AI comment and links."""
    bot = getattr(message, "bot", None)
    if bot is None or not hasattr(message, "reply_photo"):
        return False
    try:
        avatar_a, avatar_b = await fetch_avatar(bot, view.user_a_id), await fetch_avatar(bot, view.user_b_id)
        main_event = max(view.shared_events, key=lambda item: item.importance_score).title if view.shared_events else None
        png = await render_pair_card_async(PairCardData(
            user_a_id=view.user_a_id,
            user_b_id=view.user_b_id,
            user_a_name=view.user_a_name,
            user_b_name=view.user_b_name,
            level=view.level,
            archetype=view.archetype,
            affinity=view.affinity,
            tension=view.tension,
            reciprocity=view.reciprocity,
            trend=view.trend,
            weekly=tuple(bucket.events for bucket in dynamics.weeks) if dynamics else (),
            phase=dynamics.phase if dynamics and dynamics.total_events else None,
            main_event=main_event,
            avatar_a=avatar_a,
            avatar_b=avatar_b,
        ))
    except Exception as exc:
        logging.warning("[social_graph] pair card render failed: %s", exc, exc_info=True)
        return False

    parts = [f"❤️ <b>{escape(_pair_name(view))}</b>"]
    reactions = _reactions_line(view)
    if reactions:
        parts.append(reactions)
    parts.append(escape(comment))
    if main_event_html:
        parts.append(main_event_html)
    caption = "\n\n".join(parts)
    if len(caption) > CAPTION_LIMIT:
        caption = f"❤️ <b>{escape(_pair_name(view))}</b>\n\n{escape(comment[:700])}"
    try:
        await message.reply_photo(
            BufferedInputFile(png, filename="relationship.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=relationship_keyboard(view),
        )
    except Exception as exc:
        logging.warning("[social_graph] pair card send failed, falling back to text: %s", exc)
        return False
    return True


@router.message(lambda message: _is_command(message, "мои отношения"))
async def handle_my_relationships(message: types.Message):
    if not await _ensure_available(message):
        return
    await send_user_relationships(message, message.chat.id, message.from_user.id)


async def send_user_relationships(
    message: types.Message,
    chat_id: int,
    user_id: int,
    *,
    subject_name: str | None = None,
) -> None:
    views = await get_relationships(chat_id, user_id=user_id)
    if not views:
        if subject_name:
            await message.reply(f"У {escape(subject_name)} пока не накопилось отношений, которые можно уверенно показать.")
        else:
            await message.reply("У тебя пока не накопилось отношений, которые можно уверенно показать.")
        return
    header = f"❤️ <b>Главные отношения: {escape(subject_name)}</b>" if subject_name else "❤️ <b>Твои главные отношения</b>"
    lines = [header, ""]
    for index, view in enumerate(views[:6], 1):
        lines.append(
            f"{index}. <b>{escape(_other_name(view, user_id))}</b> — "
            f"уровень {view.level}, {escape(view.archetype)}; {escape(view.trend)}."
        )
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(lambda message: _is_command(message, "отношения чата"))
async def handle_chat_relationships(message: types.Message):
    if not await _ensure_available(message):
        return
    await send_chat_relationships(message, message.chat.id)


async def send_chat_relationships(message: types.Message, chat_id: int) -> None:
    views = await get_relationships(chat_id)
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
    lines.append(f"❤️ Самые близкие: <b>{escape(_pair_name(closest))}</b> — {escape(closest.archetype)}")
    if conflict.tension > 0:
        lines.append(f"⚔️ Самые напряжённые: <b>{escape(_pair_name(conflict))}</b> — {escape(conflict.archetype)}")
    if mutual is not None:
        lines.append(f"🤝 Самые взаимные: <b>{escape(_pair_name(mutual))}</b> — {round(mutual.reciprocity * 100)}%")
    if one_sided is not None and one_sided.reciprocity < 0.65:
        lines.append(f"🫠 Самая односторонняя связь: <b>{escape(_pair_name(one_sided))}</b>")
    if rising.recent_7d > 0:
        lines.append(f"📈 Быстрее всех оживают: <b>{escape(_pair_name(rising))}</b> — {escape(rising.trend)}")
    if strongest is not closest:
        lines.append(f"🏛 Главная институция: <b>{escape(_pair_name(strongest))}</b> — уровень {strongest.level}/15")
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(lambda message: _is_pair_command(message, "история отношений", "история"))
@ai_feature("история отношений")
async def handle_relationship_history(message: types.Message):
    if not await _ensure_available(message):
        return
    command = _history_command(message)
    pair = await _resolve_relationship_pair(message, command)
    if pair is None:
        await message.reply("Ответь командой «история» на сообщение человека или напиши «история @user» / «история @user1 @user2».")
        return

    await send_relationship_history(message, message.chat.id, *pair)


@ai_feature("история отношений", only_if_unset=True)
async def send_relationship_history(message: types.Message, chat_id: int, user_a_id: int, user_b_id: int) -> None:
    history = await get_integrated_relationship_history(chat_id, user_a_id, user_b_id)
    if history is None:
        await message.reply("📖 История пока короткая. Эти двое существуют в одном чате, но сериал ещё не начался.")
        return

    view = history.view
    meaningful_timeline = tuple(
        item for item in history.timeline if item.event_type != "relationship_snapshot"
    )
    lines = [f"📖 <b>{escape(_pair_name(view))}</b>", ""]
    dynamics_line = _dynamics_line(history.dynamics)
    if dynamics_line:
        lines.extend((dynamics_line, ""))

    if meaningful_timeline:
        narrative = await narrate_relationship_history(view, meaningful_timeline, str(chat_id))
        if narrative:
            lines.extend((escape(narrative), ""))
        lines.extend(_timeline_html(meaningful_timeline))
    else:
        lines.append(
            "Подробная история пока не накоплена. Текущее состояние уже считается, "
            "но значимых сохранённых переходов или совместных событий ещё нет."
        )

    lines.extend((
        "",
        "<b>Сейчас</b>",
        f"Уровень <b>{view.level}/15</b> — «<b>{escape(view.archetype)}</b>»; {escape(view.trend)}.",
    ))
    await message.reply(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=relationship_keyboard(view, include_history=False),
    )


async def send_pair_episodes(message: types.Message, chat_id: int, user_a_id: int, user_b_id: int) -> None:
    """All shared Chronicle episodes of a pair, newest first, with links."""
    view = await get_relationship(chat_id, user_a_id, user_b_id)
    if view is None or not view.shared_events:
        await message.reply("🎬 Совместных эпизодов в Летописи у этой пары пока нет.")
        return
    lines = [f"🎬 <b>Эпизоды: {escape(_pair_name(view))}</b>", ""]
    events = sorted(view.shared_events, key=lambda item: item.event_started_at, reverse=True)
    for event in events[:10]:
        title = escape(event.title)
        message_id = event.anchor_message_id or (event.source_message_ids[0] if event.source_message_ids else None)
        link = _telegram_message_link(chat_id, message_id)
        if link:
            title = f'<a href="{link}">{title}</a>'
        when = event.event_started_at
        lines.append(f"• <b>{when.day} {_MONTHS_GENITIVE[when.month - 1]} {when.year}</b> — {title}")
        lines.append(escape(event.summary))
    if len(events) > 10:
        lines.append(f"\n…и ещё {len(events) - 10} эпизодов.")
    await message.reply("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(lambda message: _is_command(message, "мои связи"))
@ai_feature("мои связи")
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
@ai_feature("центровой")
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
