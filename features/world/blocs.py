"""Blocs — multilateral alliances with admission by vote.

A state founds a bloc with a name.  Another state applies; every current member
gets a ballot in its chat, and the application passes with a simple majority
(rejection once a majority is out of reach, expiry after 24 hours).  Each other
member adds +2 authority (max +10).  A state belongs to at most one bloc.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from aiogram.utils.keyboard import InlineKeyboardBuilder

from features.world.delivery import deliver_text
from features.world.models import WorldState
from features.world.service import WorldService, get_world_service
from infrastructure.persistence.sqlite_world_blocs import MAX_BLOC_MEMBERS, BlocApplication, WorldBloc


APPLICATION_TTL = timedelta(hours=24)


def ballot_markup(application_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=f"worldbloc:vote:{application_id}:yes")
    builder.button(text="❌ Отказать", callback_data=f"worldbloc:vote:{application_id}:no")
    builder.adjust(2)
    return builder.as_markup()


async def list_blocs(service: WorldService) -> list[WorldBloc]:
    if service.blocs is None:
        return []
    return await asyncio.to_thread(service.blocs.list_blocs)


async def bloc_of(service: WorldService, world_id: int) -> WorldBloc | None:
    if service.blocs is None:
        return None
    return await asyncio.to_thread(service.blocs.get_bloc_of, world_id)


async def create_bloc(service: WorldService, founder: WorldState, name: str) -> tuple[str, WorldBloc | None]:
    if service.blocs is None:
        return "unavailable", None
    status, bloc = await asyncio.to_thread(service.blocs.create_bloc, founder.world_id, name)
    if status == "created" and bloc is not None:
        await service.record_event(
            "bloc_created",
            actor_state=founder.world_id,
            payload={"bloc_id": bloc.bloc_id, "name": bloc.name},
        )
    return status, bloc


async def leave_bloc(service: WorldService, state: WorldState) -> tuple[str, WorldBloc | None]:
    if service.blocs is None:
        return "unavailable", None
    status, bloc = await asyncio.to_thread(service.blocs.leave_bloc, state.world_id)
    if bloc is not None and status in {"left", "dissolved"}:
        await service.record_event(
            "bloc_left",
            actor_state=state.world_id,
            payload={"bloc_id": bloc.bloc_id, "name": bloc.name},
        )
        if status == "dissolved":
            await service.record_event(
                "bloc_dissolved",
                actor_state=state.world_id,
                payload={"bloc_id": bloc.bloc_id, "name": bloc.name},
            )
    return status, bloc


async def apply_to_bloc(
    bot,
    service: WorldService,
    applicant: WorldState,
    bloc_id: int,
) -> tuple[str, BlocApplication | None, WorldBloc | None]:
    if service.blocs is None:
        return "unavailable", None, None
    status, application = await asyncio.to_thread(
        service.blocs.apply,
        bloc_id,
        applicant.world_id,
        expires_at=datetime.now(timezone.utc) + APPLICATION_TTL,
    )
    bloc = await asyncio.to_thread(service.blocs.get_bloc, bloc_id)
    if status != "created" or application is None or bloc is None:
        return status, application, bloc
    for member_id in application.electorate:
        member = await service.get_state_by_world_id(member_id)
        await deliver_text(
            bot,
            service,
            member,
            f"🧱 Заявка в блок «{bloc.name}»\n\n"
            f"Государство №{applicant.world_id} — {applicant.title} хочет вступить в блок.\n"
            f"Нужно голосов «за»: {application.needed} из {len(application.electorate)}. "
            "Голосуют администраторы или посол. Заявка живёт сутки.",
            reply_markup=ballot_markup(application.application_id),
        )
    return status, application, bloc


async def vote(
    bot,
    service: WorldService,
    voter: WorldState,
    application_id: int,
    approve: bool,
) -> tuple[str, BlocApplication | None]:
    if service.blocs is None:
        return "unavailable", None
    status, application = await asyncio.to_thread(service.blocs.vote, application_id, voter.world_id, approve)
    if application is None or status not in {"accepted", "rejected"}:
        return status, application
    bloc = await asyncio.to_thread(service.blocs.get_bloc, application.bloc_id)
    applicant = await service.get_state_by_world_id(application.world_id)
    name = bloc.name if bloc else "блок"
    if status == "accepted":
        await service.record_event(
            "bloc_joined",
            actor_state=application.world_id,
            payload={"bloc_id": application.bloc_id, "name": name},
            dedupe_key=f"bloc_joined:{application.application_id}",
        )
        await deliver_text(bot, service, applicant, f"🧱 Вас приняли в блок «{name}». Добро пожаловать в коллективную безответственность.")
        if bloc is not None:
            for member_id in bloc.members:
                if member_id == application.world_id:
                    continue
                member = await service.get_state_by_world_id(member_id)
                await deliver_text(
                    bot,
                    service,
                    member,
                    f"🧱 В блок «{name}» вступило государство №{application.world_id}"
                    f"{' — ' + applicant.title if applicant else ''}.",
                )
    else:
        await service.record_event(
            "bloc_application_rejected",
            actor_state=application.world_id,
            payload={"bloc_id": application.bloc_id, "name": name},
            dedupe_key=f"bloc_rejected:{application.application_id}",
        )
        await deliver_text(bot, service, applicant, f"🧱 Блок «{name}» отклонил вашу заявку. Не очень-то и хотелось.")
    return status, application


async def expire_bloc_applications(bot, service: WorldService | None = None) -> int:
    service = service or get_world_service()
    if service.blocs is None:
        return 0
    expired = await asyncio.to_thread(service.blocs.expire_applications, datetime.now(timezone.utc))
    for application in expired:
        applicant = await service.get_state_by_world_id(application.world_id)
        await deliver_text(
            bot,
            service,
            applicant,
            "🧱 Заявка в блок протухла: члены блока так и не собрались проголосовать.",
        )
    return len(expired)


def format_blocs(
    blocs: list[WorldBloc],
    states: dict[int, WorldState],
    current_world_id: int | None = None,
) -> str:
    if not blocs:
        return (
            "🧱 Блоков пока нет.\n\n"
            "Основать: «упупа создай блок <название>». Каждый союзник по блоку даёт +2 к авторитету (до +10)."
        )
    lines = ["🧱 Блоки Мира Упупы", ""]
    for bloc in blocs:
        marker = " ← вы здесь" if current_world_id in bloc.members else ""
        lines.append(f"«{bloc.name}» — {len(bloc.members)}/{MAX_BLOC_MEMBERS}{marker}")
        for member_id in bloc.members:
            state = states.get(member_id)
            founder = " (основатель)" if member_id == bloc.founder_state else ""
            lines.append(f"  • №{member_id} {state.title if state else ''}{founder}".rstrip())
        lines.append("")
    lines.append("Основать: «упупа создай блок <название>». Вступление — голосованием членов блока.")
    return "\n".join(lines).rstrip()
