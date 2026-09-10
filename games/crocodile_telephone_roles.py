"""Role-separated lobby and turn ordering for broken-telephone Crocodile."""

from __future__ import annotations

import html
import logging
from types import SimpleNamespace
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.settings import ADMIN_ID
from games import crocodile_modes, crocodile_party_controls


ROLE_TEXT = "text"
ROLE_DRAW = "draw"
_ROLE_FIELD = "telephone_roles"

_configured = False
_original_start_telephone = None
_original_handle_telephone_callback = None
_original_party_status_text = None
_original_skip_telephone = None


def _user_id(value: Any) -> int | None:
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _ensure_roles(game: dict) -> dict[str, str]:
    """Normalize roles and migrate legacy one-list lobbies by current parity."""
    raw = game.get(_ROLE_FIELD)
    roles = dict(raw) if isinstance(raw, dict) else {}
    players = game.get("players") or []
    valid_ids: set[str] = set()
    for index, row in enumerate(players):
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        user_id = _user_id(row[0])
        if user_id is None:
            continue
        key = str(user_id)
        valid_ids.add(key)
        role = str(roles.get(key) or "")
        if role not in {ROLE_TEXT, ROLE_DRAW}:
            roles[key] = ROLE_TEXT if index % 2 == 0 else ROLE_DRAW
    roles = {
        key: role
        for key, role in roles.items()
        if key in valid_ids and role in {ROLE_TEXT, ROLE_DRAW}
    }
    game[_ROLE_FIELD] = roles
    return roles


def _members(game: dict, role: str) -> list[tuple[int, str]]:
    roles = _ensure_roles(game)
    result: list[tuple[int, str]] = []
    for row in game.get("players") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        user_id = _user_id(row[0])
        if user_id is None or roles.get(str(user_id)) != role:
            continue
        result.append((user_id, str(row[1])))
    return result


def role_counts(game: dict) -> tuple[int, int]:
    return len(_members(game, ROLE_TEXT)), len(_members(game, ROLE_DRAW))


def role_balance(game: dict) -> tuple[bool, str]:
    """A valid chain starts with text and alternates, so text is equal or +1."""
    text_count, draw_count = role_counts(game)
    total = text_count + draw_count
    if total < crocodile_modes.TELEPHONE_MIN_PLAYERS:
        return False, f"Нужно минимум {crocodile_modes.TELEPHONE_MIN_PLAYERS} человека."
    if draw_count == 0:
        return False, "Нужен хотя бы один человек, который будет рисовать."
    if text_count not in {draw_count, draw_count + 1}:
        return False, (
            "Сначала выровняйте роли: пишущих слова/догадки должно быть столько же, "
            "сколько рисующих, либо на одного больше."
        )
    return True, "Роли сбалансированы."


def _role_names(game: dict, role: str) -> str:
    names = [html.escape(name) for _user_id, name in _members(game, role)]
    return ", ".join(names) if names else "—"


def telephone_lobby_text(game: dict) -> str:
    text_count, draw_count = role_counts(game)
    ready, reason = role_balance(game)
    balance = "✅ Можно начинать." if ready else f"⏳ Пока нельзя начинать: {html.escape(reason)}"
    return (
        "☎️ <b>ИСПОРЧЕННЫЙ КРАКАДИЛ</b>\n\n"
        "Перед стартом разделитесь на две группы. Каждый участник всю цепочку "
        "остаётся в выбранной роли.\n\n"
        f"✍️ <b>Только слова и догадки ({text_count}):</b> {_role_names(game, ROLE_TEXT)}\n"
        f"🎨 <b>Только рисование ({draw_count}):</b> {_role_names(game, ROLE_DRAW)}\n\n"
        "Для правильной цепочки пишущих должно быть столько же, сколько рисующих, "
        "либо на одного больше. Нажатие другой роли просто переносит тебя в другую группу.\n"
        f"Минимум {crocodile_modes.TELEPHONE_MIN_PLAYERS}, максимум {crocodile_modes.TELEPHONE_MAX_PLAYERS}.\n\n"
        f"{balance}"
    )


def telephone_lobby_keyboard(chat_id: int | str) -> InlineKeyboardMarkup:
    cid = str(int(chat_id))
    game = crocodile_modes.telephone_games.get(cid) or {}
    text_count, draw_count = role_counts(game) if game else (0, 0)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✍️ Только слова ({text_count})",
                    callback_data=f"ctel_role_text_{cid}",
                ),
                InlineKeyboardButton(
                    text=f"🎨 Только рисовать ({draw_count})",
                    callback_data=f"ctel_role_draw_{cid}",
                ),
            ],
            [InlineKeyboardButton(text="▶️ Начать", callback_data=f"ctel_start_{cid}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"ctel_cancel_{cid}")],
        ]
    )


def _persist() -> None:
    try:
        from games import crocodile_party_state as party_state

        party_state.persist_party_modes(force=True)
    except Exception:
        logging.exception("[croc-phone-roles] failed to persist role lobby")


async def _refresh_lobby_message(callback, game: dict) -> None:
    text = telephone_lobby_text(game)
    markup = telephone_lobby_keyboard(callback.message.chat.id)
    editor = getattr(callback.message, "edit_text", None)
    if callable(editor):
        try:
            await editor(text, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            logging.exception("[croc-phone-roles] failed to edit lobby message")
    sender = getattr(callback.message, "answer", None)
    if callable(sender):
        await sender(text, parse_mode="HTML", reply_markup=markup)


def _ordered_players(game: dict) -> list[tuple[int, str]]:
    texts = _members(game, ROLE_TEXT)
    draws = _members(game, ROLE_DRAW)
    ordered: list[tuple[int, str]] = []
    for index in range(max(len(texts), len(draws))):
        if index < len(texts):
            ordered.append(texts[index])
        if index < len(draws):
            ordered.append(draws[index])
    return ordered


def _is_host_or_admin(game: dict, user_id: Any) -> bool:
    uid = _user_id(user_id)
    if uid is None:
        return False
    try:
        return uid in {int(game.get("host_id") or 0), int(ADMIN_ID)}
    except (TypeError, ValueError):
        return uid == int(game.get("host_id") or 0)


async def start_telephone_with_roles(message) -> Any:
    """Keep existing start guards but render a role-aware lobby from the first card."""
    cid = str(message.chat.id)
    if cid in crocodile_modes.telephone_games:
        return await _original_start_telephone(message)

    original_answer = message.answer

    async def answer(text, *args, **kwargs):
        game = crocodile_modes.telephone_games.get(cid)
        if game and game.get("phase") == "lobby":
            roles = _ensure_roles(game)
            host_id = _user_id(game.get("host_id"))
            if host_id is not None:
                roles[str(host_id)] = roles.get(str(host_id), ROLE_TEXT)
            _persist()
            kwargs["parse_mode"] = "HTML"
            kwargs["reply_markup"] = telephone_lobby_keyboard(message.chat.id)
            return await original_answer(telephone_lobby_text(game), *args, **kwargs)
        return await original_answer(text, *args, **kwargs)

    proxy = SimpleNamespace(
        chat=message.chat,
        from_user=message.from_user,
        answer=answer,
        reply=getattr(message, "reply", answer),
    )
    return await _original_start_telephone(proxy)


async def handle_telephone_callback_with_roles(callback) -> Any:
    data = callback.data or ""
    role = None
    cid = None
    if data.startswith("ctel_role_text_"):
        role = ROLE_TEXT
        cid = data[len("ctel_role_text_"):]
    elif data.startswith("ctel_role_draw_"):
        role = ROLE_DRAW
        cid = data[len("ctel_role_draw_"):]

    if role is not None and cid is not None:
        game = crocodile_modes.telephone_games.get(cid)
        if not game or game.get("phase") != "lobby":
            return await callback.answer("Эта цепочка уже закрыта")
        user = getattr(callback, "from_user", None)
        user_id = _user_id(getattr(user, "id", None))
        if user_id is None:
            return await callback.answer("Не удалось определить игрока", show_alert=True)

        players = game.setdefault("players", [])
        ids = {_user_id(row[0]) for row in players if isinstance(row, (list, tuple)) and row}
        is_new = user_id not in ids
        if is_new:
            if len(players) >= crocodile_modes.TELEPHONE_MAX_PLAYERS:
                return await callback.answer("Мест больше нет", show_alert=True)
            players.append((user_id, str(getattr(user, "full_name", None) or f"Игрок {user_id}")))

        roles = _ensure_roles(game)
        previous = None if is_new else roles.get(str(user_id))
        roles[str(user_id)] = role
        game[_ROLE_FIELD] = roles
        _persist()

        if previous == role:
            answer = "Ты уже в этой группе"
        elif role == ROLE_TEXT:
            answer = "Теперь ты пишешь слова и догадки"
        else:
            answer = "Теперь ты только рисуешь"
        await callback.answer(answer)
        await _refresh_lobby_message(callback, game)
        return

    if data.startswith("ctel_join_"):
        cid = data[len("ctel_join_"):]
        game = crocodile_modes.telephone_games.get(cid)
        if game and game.get("phase") == "lobby":
            _ensure_roles(game)
            await callback.answer(
                "Теперь сначала выбери роль: слова или рисование.",
                show_alert=True,
            )
            await _refresh_lobby_message(callback, game)
            return

    if data.startswith("ctel_start_"):
        cid = data[len("ctel_start_"):]
        game = crocodile_modes.telephone_games.get(cid)
        if game and game.get("phase") == "lobby" and _is_host_or_admin(
            game, getattr(getattr(callback, "from_user", None), "id", None)
        ):
            ready, reason = role_balance(game)
            if not ready:
                return await callback.answer(reason, show_alert=True)
            game["players"] = _ordered_players(game)
            _persist()

    return await _original_handle_telephone_callback(callback)


def party_status_text_with_roles(chat_id: int | str) -> str:
    cid = str(chat_id)
    game = crocodile_modes.telephone_games.get(cid)
    if game and game.get("phase") == "lobby":
        text_count, draw_count = role_counts(game)
        ready, _reason = role_balance(game)
        suffix = "баланс готов" if ready else "баланс ещё не готов"
        return (
            "☎️ Сейчас собирается испорченный телефон: "
            f"слова {text_count}, рисование {draw_count}; {suffix}."
        )
    return _original_party_status_text(chat_id)


def _role_of(game: dict, user_id: Any) -> str | None:
    uid = _user_id(user_id)
    if uid is None:
        return None
    return _ensure_roles(game).get(str(uid))


def _alternating_remaining(
    game: dict,
    remaining: list[tuple[int, str]],
    required: str,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    pools = {
        ROLE_TEXT: [row for row in remaining if _role_of(game, row[0]) == ROLE_TEXT],
        ROLE_DRAW: [row for row in remaining if _role_of(game, row[0]) == ROLE_DRAW],
    }
    ordered: list[tuple[int, str]] = []
    role = required
    while pools[role]:
        ordered.append(pools[role].pop(0))
        role = ROLE_DRAW if role == ROLE_TEXT else ROLE_TEXT
    dropped = pools[ROLE_TEXT] + pools[ROLE_DRAW]
    return ordered, dropped


async def skip_telephone_with_roles(chat_id: str, game: dict) -> str:
    """Skip without ever assigning a text-only participant to a drawing turn or vice versa."""
    roles = game.get(_ROLE_FIELD)
    if not isinstance(roles, dict) or not roles:
        return await _original_skip_telephone(chat_id, game)
    if game.get("phase") != "playing":
        return "Цепочка ещё не идёт."

    step = int(game.get("step") or 0)
    players = list(game.get("players") or [])
    if step >= len(players):
        return "Пропускать уже некого."

    skipped_id, skipped_name = players.pop(step)
    game["players"] = players
    roles = _ensure_roles(game)
    roles.pop(str(_user_id(skipped_id) or skipped_id), None)
    game[_ROLE_FIELD] = roles

    required = ROLE_TEXT if step % 2 == 0 else ROLE_DRAW
    prefix = players[:step]
    ordered, dropped = _alternating_remaining(game, players[step:], required)
    game["players"] = prefix + ordered
    _ensure_roles(game)

    key = crocodile_modes._session_key(chat_id, f"t{step}")
    crocodile_modes.canvas_sessions.pop(key, None)
    _persist()
    await crocodile_party_controls._close_synthetic_room(chat_id, f"t{step}")

    text = f"⏭ <b>{html.escape(str(skipped_name))}</b> пропущен."
    if dropped:
        dropped_names = ", ".join(html.escape(str(name)) for _uid, name in dropped)
        text += (
            " Чтобы никому не выдавать чужую роль, из непарного хвоста этой цепочки "
            f"также выбывает: <b>{dropped_names}</b>."
        )
    await crocodile_party_controls.bot.send_message(int(chat_id), text, parse_mode="HTML")

    if step >= len(game.get("players") or []):
        await crocodile_modes._finish_telephone(chat_id, game)
    else:
        await crocodile_modes._send_telephone_step(chat_id, game)
    return f"Пропущен: {skipped_name}"


def configure_crocodile_telephone_roles() -> None:
    """Install role lobby last so it sees admin, resilience and permission wrappers."""
    global _configured
    global _original_start_telephone, _original_handle_telephone_callback
    global _original_party_status_text, _original_skip_telephone
    if _configured:
        return

    _original_start_telephone = crocodile_modes.start_telephone
    _original_handle_telephone_callback = crocodile_modes.handle_telephone_callback
    _original_party_status_text = crocodile_party_controls.party_status_text
    _original_skip_telephone = crocodile_party_controls._skip_telephone

    crocodile_modes.start_telephone = start_telephone_with_roles
    crocodile_modes.handle_telephone_callback = handle_telephone_callback_with_roles
    crocodile_modes._telephone_lobby_keyboard = telephone_lobby_keyboard
    crocodile_party_controls.party_status_text = party_status_text_with_roles
    crocodile_party_controls._skip_telephone = skip_telephone_with_roles
    _configured = True
