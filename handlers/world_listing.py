"""World-state listing aliases shown before the main World router."""

from aiogram import Router, types

from core.upupa_utils import normalize_upupa_command
from features.world.models import WorldState
from handlers.world import _require_world, _title


router = Router(name="world_listing")


def is_states_list_command(message: types.Message) -> bool:
    text = (message.text or "").strip()
    return text.lower() == "государства" or normalize_upupa_command(text) == "упупа миры"


def states_with_current(
    current: WorldState,
    others: tuple[WorldState, ...],
) -> tuple[WorldState, ...]:
    return tuple(sorted((current, *others), key=lambda state: state.world_id))


def format_current_states(states: tuple[WorldState, ...]) -> str:
    lines = ["🌍 Текущие государства:"]
    lines.extend(f"№{state.world_id} — {state.title}" for state in states)
    return "\n".join(lines)


@router.message(is_states_list_command)
async def states_list(message: types.Message):
    service = await _require_world(message)
    if service is None:
        return

    current = await service.get_state(message.chat.id, _title(message))
    others = await service.list_states(message.chat.id, _title(message))
    if current is None or not current.enabled or others is None:
        await message.reply("🌍 Государство выключено.")
        return

    await message.reply(format_current_states(states_with_current(current, others)))
