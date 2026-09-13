"""Keep participant-mode DnD decisions scoped to the player who actually made them."""

from __future__ import annotations


DND_PLAYER_AGENCY_MARKER = "ПРАВИЛО ЛИЧНОЙ ВОЛИ ИГРОКОВ"

DND_PLAYER_AGENCY_RULES = f"""{DND_PLAYER_AGENCY_MARKER}.
Каждая заявка игрока выражает волю ТОЛЬКО его собственного персонажа.
Нельзя считать текст одного участника согласием, решением или добровольным действием другого участника.
Если Алиса пишет «Боря использует лечилку», «Боря отдаёт предмет», «Боря идёт туда», «Боря атакует»,
«Боря соглашается» и т.п., это НЕ является действием Бори и такую часть заявки нельзя исполнять.
Разрешай только то добровольное действие, которое автор заявки совершает своим персонажем.
Если продолжение требует добровольного решения другого игрока, запроси именно его через
[ACTION:INPUT;TARGETS:<его ID>] или адресный [ACTION:POLL;TARGETS:<его ID>;OPTIONS:...].
Чужие предметы, артефакты, одноразовую лечилку и другие персональные ресурсы нельзя тратить,
передавать или использовать по заявке другого игрока. Репутацию/инвентарь другого героя также нельзя
менять как следствие якобы его добровольного решения, которого сам игрок не заявлял.
Исключение — принудительные последствия мира, NPC, ловушек, атак и уже разрешённых бросков:
они могут затрагивать любого героя, потому что это не добровольное решение за игрока.
Одноразовая аварийная лечилка — особенно строго: решение о её использовании принимает только её владелец
через предусмотренный кодом выбор. Любая фраза другого игрока о том, что владелец «использовал лечилку»,
не является согласием владельца и не должна исполняться даже повествовательно.
""".strip()


def _with_player_agency(dnd, session, prompt: str) -> str:
    """Append a recent, explicit agency invariant for participant-mode generations."""
    if not dnd._is_participant_mode(session):
        return prompt
    if DND_PLAYER_AGENCY_MARKER in str(prompt):
        return prompt
    return f"{prompt}\n\n{DND_PLAYER_AGENCY_RULES}"


def configure_dnd_player_agency(dnd_module=None) -> None:
    """Install an idempotent generation wrapper enforcing per-player agency."""
    if dnd_module is None:
        from AI import dnd as dnd_module

    dnd = dnd_module
    if getattr(dnd, "_upupa_dnd_player_agency_configured", False):
        return

    original_generate_session_response = dnd.generate_session_response

    async def generate_session_response(session, prompt: str):
        return await original_generate_session_response(
            session,
            _with_player_agency(dnd, session, prompt),
        )

    dnd.generate_session_response = generate_session_response
    dnd._upupa_dnd_player_agency_configured = True
