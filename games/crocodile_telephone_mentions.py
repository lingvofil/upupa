"""Mention the active player on every broken-telephone turn."""

from __future__ import annotations

import html

from games import crocodile_modes, crocodile_party_controls


_configured = False
_original_send_telephone_step = None


async def send_telephone_step_with_mention(chat_id: str, game: dict) -> None:
    """Render a telephone step with a real Telegram user mention."""
    step = int(game["step"])
    players = game["players"]
    if step >= len(players):
        await crocodile_modes._finish_telephone(chat_id, game)
        return

    user_id, name = players[step]
    suffix = f"t{step}"
    key = crocodile_modes._session_key(chat_id, suffix)
    if step == 0:
        ui_mode = "text"
        prompt = (
            "Загадай слово или короткую фразу. "
            "Следующий игрок увидит только её и будет рисовать."
        )
        word = ""
        reference = None
    elif step % 2 == 1:
        ui_mode = "draw"
        previous = game["chain"][-1]
        word = str(previous["value"])
        prompt = ""
        reference = None
    else:
        ui_mode = "text"
        previous = game["chain"][-1]
        word = ""
        prompt = "Что здесь нарисовано? Напиши свою догадку."
        reference = previous.get("image")

    crocodile_modes.canvas_sessions[key] = {
        "chat_id": str(chat_id),
        "drawer_id": user_id,
        "drawer_name": name,
        "mode": "telephone",
        "telephone_chat_id": str(chat_id),
        "telephone_step": step,
        "ui_mode": ui_mode,
        "word": word,
        "prompt": prompt,
        "reference_image": reference,
        "suppress_chat_preview": True,
        "last_preview_bytes": crocodile_modes._blank(),
        "last_preview_time": 0,
    }

    action = "рисуй" if ui_mode == "draw" else ("загадывай" if step == 0 else "угадывай")
    mention = (
        f'<a href="tg://user?id={int(user_id)}">'
        f"{html.escape(str(name))}</a>"
    )
    await crocodile_modes.bot.send_message(
        int(chat_id),
        f"☎️ Ход {mention}: {action}. Остальные не подглядывают.",
        parse_mode="HTML",
        reply_markup=crocodile_modes._canvas_button(
            chat_id,
            suffix,
            "✍️ Открыть свой ход",
        ),
    )


def configure_crocodile_telephone_mentions() -> None:
    """Replace the base telephone sender while keeping resilience wrappers."""
    global _configured, _original_send_telephone_step
    if _configured:
        return

    _original_send_telephone_step = crocodile_party_controls._original_send_telephone_step
    if _original_send_telephone_step is None:
        raise RuntimeError("Crocodile party controls must be configured first")

    # Party controls intentionally call this captured base sender before adding
    # persistence and skip/cancel controls. Replacing only the captured sender
    # preserves all of that behaviour while changing the turn announcement.
    crocodile_party_controls._original_send_telephone_step = send_telephone_step_with_mention

    # Install this last so direct and unified-menu skips see the final admin/UI
    # wrappers while enforcing one identical permission rule.
    from games.crocodile_telephone_skip_permissions import (
        configure_crocodile_telephone_skip_permissions,
    )

    configure_crocodile_telephone_skip_permissions()
    _configured = True
