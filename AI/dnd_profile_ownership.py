"""Hard ownership guard for DnD character-profile buttons."""
from __future__ import annotations

from aiogram import BaseMiddleware


def profile_owner_id(callback_data: str | None) -> int | None:
    parts = str(callback_data or "").split(":")
    if len(parts) >= 4 and parts[0] == "dnd" and parts[1] == "prof" and parts[2].isdigit():
        return int(parts[2])
    return None


class DndProfileOwnershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        owner_id = profile_owner_id(getattr(event, "data", None))
        if owner_id is not None:
            actor_id = getattr(getattr(event, "from_user", None), "id", None)
            if actor_id is None or int(actor_id) != owner_id:
                await event.answer("Это не твой персонаж.", show_alert=True)
                return None
        return await handler(event, data)


def install_dnd_profile_ownership(router) -> None:
    if getattr(router, "_upupa_dnd_profile_ownership_installed", False):
        return
    router.callback_query.outer_middleware(DndProfileOwnershipMiddleware())
    router._upupa_dnd_profile_ownership_installed = True


__all__ = [
    "DndProfileOwnershipMiddleware",
    "install_dnd_profile_ownership",
    "profile_owner_id",
]
