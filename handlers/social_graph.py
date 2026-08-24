"""Telegram transport for the social graph commands and reaction updates."""

from __future__ import annotations

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
from features.social_graph.rendering import render_graph_png_async
from features.social_graph.service import (
    REPLY_WEIGHT,
    capture_reaction,
    get_graph_data,
    is_social_graph_enabled,
)


router = Router(name="social_graph")


def _is_command(message: types.Message, command: str) -> bool:
    return bool(
        message.text
        and message.from_user
        and message.from_user.id not in BLOCKED_USERS
        and message.text.strip().lower() == command
    )


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
