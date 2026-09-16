"""Concise feedback for DnD rolls attempted by the wrong participant."""
from __future__ import annotations


WRONG_ROLL_MESSAGE = "Этот бросок не твой."


def install_dnd_roll_feedback(dnd) -> None:
    """Normalize the wrong-player message for combat attack rolls."""
    from AI import dnd_combat as combat

    if getattr(combat, "_upupa_dnd_roll_feedback_installed", False):
        return

    original_resolve_player_roll = combat._resolve_player_roll

    async def resolve_player_roll(dnd_module, message, session):
        pending = getattr(session, "pending_roll", None) or {}
        if str(pending.get("type") or "").upper() == "ATTACK":
            user_id = int(message.from_user.id)
            if not dnd_module._can_user_act(
                session,
                user_id,
                pending.get("target_user_ids") or [],
            ):
                await message.answer(WRONG_ROLL_MESSAGE)
                return
        return await original_resolve_player_roll(dnd_module, message, session)

    combat._resolve_player_roll = resolve_player_roll
    combat._upupa_dnd_roll_feedback_installed = True


__all__ = ["WRONG_ROLL_MESSAGE", "install_dnd_roll_feedback"]
