"""Welcome message for a state that just joined the World of Upupa."""

from __future__ import annotations

from features.world.models import WorldState
from features.world.service import WorldService


async def build_welcome_text(service: WorldService, state: WorldState, *, returning: bool = False) -> str:
    authority = await service.get_authority_map()
    others = [item for item in await service.list_all_states() if item.world_id != state.world_id]
    others.sort(key=lambda item: -(authority[item.world_id].total if item.world_id in authority else 0))
    lines = [
        f"🌍 {'С возвращением' if returning else 'Добро пожаловать'} в Мир Упупы, государство №{state.world_id}!",
        "",
        "Теперь этот чат — государство среди других чатов: союзы, войны с битвами за мобилизацию, "
        "санкции, Международный суд, визиты, блоки и еженедельная газета.",
    ]
    if others:
        lines.extend(["", "С кем стоит познакомиться:"])
        for neighbour in others[:3]:
            score = authority[neighbour.world_id].total if neighbour.world_id in authority else 50
            lines.append(f"• №{neighbour.world_id} — {neighbour.title} (авторитет {score})")
    else:
        lines.extend(["", "Пока вы единственное государство. Позовите в Мир соседние чаты."])
    lines.extend(
        [
            "",
            "С чего начать: «упупа миры» → «Дипломатия» → предложить союз или позвать соседей в гости. "
            "Админы могут назначить посла: реплай «упупа назначь посла».",
        ]
    )
    return "\n".join(lines)
