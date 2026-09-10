"""Party modes layered on top of the existing Crocodile Mini App."""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import secrets
import time
from typing import Any

from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from core.loader import bot
from core.settings import API_TOKEN
from features.crocodile_archive import record_drawing
from games import crocodile
from games.webapp_auth import (
    WebAppAuthError,
    authorize_crocodile_drawer,
    normalize_crocodile_room,
)


DUEL_VOTE_SECONDS = 60
TELEPHONE_MIN_PLAYERS = 3
TELEPHONE_MAX_PLAYERS = 8

# Synthetic canvas sessions do not live in crocodile.game_sessions, so the
# existing persistence loop remains limited to ordinary/duo rounds.
canvas_sessions: dict[str, dict] = {}
duel_games: dict[str, dict] = {}
telephone_games: dict[str, dict] = {}

_original_authorize_socket_room = None
_original_snapshot = None
_original_final_frame = None
_original_get_game_keyboard = None
_original_handle_callback = None
_original_check_answer = None
_configured = False


def _chat_room(chat_id: int | str, suffix: str | None = None) -> str:
    value = int(chat_id)
    base = f"m{abs(value)}" if value < 0 else str(value)
    return f"{base}_{suffix}" if suffix else base


def _app_link(chat_id: int | str, suffix: str) -> str:
    room = _chat_room(chat_id, suffix)
    return (
        f"https://t.me/{crocodile.BOT_USERNAME}/{crocodile.WEB_APP_SHORT_NAME}"
        f"?startapp={room}&v={int(time.time())}"
    )


def _session_key(chat_id: int | str, suffix: str) -> str:
    return f"{int(chat_id)}:{suffix}"


def _drawer_ids(session: dict) -> set[int]:
    ids: set[int] = set()
    raw = session.get("drawer_ids")
    if isinstance(raw, (list, tuple, set)):
        for value in raw:
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                pass
    try:
        ids.add(int(session.get("drawer_id")))
    except (TypeError, ValueError):
        pass
    return {value for value in ids if value > 0}


def is_session_drawer(session: dict, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return int(user_id) in _drawer_ids(session)


def session_artist_names(session: dict) -> list[str]:
    names = session.get("drawer_names")
    if isinstance(names, list) and names:
        return [str(name) for name in names if str(name).strip()]
    name = str(session.get("drawer_name") or "Художник")
    return [name]


def _blank() -> bytes:
    return base64.b64decode(crocodile.BLANK_PNG_B64)


def _mode_button(text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data)]]
    )


def _canvas_button(chat_id: int | str, suffix: str, text: str = "🎨 Открыть холст") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, url=_app_link(chat_id, suffix))]]
    )


def get_game_keyboard_with_duo(chat_id: int) -> InlineKeyboardMarkup:
    keyboard = _original_get_game_keyboard(chat_id)
    rows = [list(row) for row in keyboard.inline_keyboard]
    rows.append(
        [InlineKeyboardButton(text="👥 Рисовать вдвоём", callback_data=f"cr_duo_{chat_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def handle_regular_callback(callback) -> Any:
    data = callback.data or ""
    if not data.startswith("cr_duo_"):
        return await _original_handle_callback(callback)

    chat_id = data[len("cr_duo_"):]
    session = crocodile.game_sessions.get(chat_id)
    if not session:
        return await callback.answer("Игра уже закончилась")
    user = callback.from_user
    if not user:
        return await callback.answer("Не удалось определить художника")

    ids = _drawer_ids(session)
    if user.id in ids:
        if len(ids) >= 2:
            return await callback.answer("Вы уже рисуете вдвоём 👥", show_alert=True)
        return await callback.answer(
            "Ты уже первый художник. Пусть кнопку нажмёт напарник.", show_alert=True
        )
    if len(ids) >= 2:
        return await callback.answer("Мольберт уже занят двумя гениями.", show_alert=True)

    primary_name = str(session.get("drawer_name") or "Художник")
    session["drawer_ids"] = [int(session.get("drawer_id") or 0), user.id]
    session["drawer_names"] = [primary_name, user.full_name]
    session["drawer_name"] = f"{primary_name} + {user.full_name}"
    session["mode"] = "duo"
    await callback.answer("Ты второй хуйдожник. Открывай холст!", show_alert=True)
    await callback.message.answer(
        f"👥 <b>{html.escape(primary_name)}</b> и <b>{html.escape(user.full_name)}</b> теперь рисуют вместе на одном холсте.",
        parse_mode="HTML",
    )


async def _authorize_socket_room_with_modes(sid, data, *, bind_room: bool = False):
    requested = data.get("room") if isinstance(data, dict) else None
    if requested:
        canonical, session_key = normalize_crocodile_room(requested)
        if ":" not in session_key:
            return await _original_authorize_socket_room(sid, data, bind_room=bind_room)
    else:
        socket_session = await crocodile.sio.get_session(sid)
        bound = socket_session.get("room")
        if not bound:
            return await _original_authorize_socket_room(sid, data, bind_room=bind_room)
        canonical, session_key = normalize_crocodile_room(bound)
        if ":" not in session_key:
            return await _original_authorize_socket_room(sid, data, bind_room=bind_room)

    socket_session = await crocodile.sio.get_session(sid)
    try:
        user_id = int(socket_session["telegram_user_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WebAppAuthError("socket has no verified Telegram user") from exc

    canonical, session_key = authorize_crocodile_drawer(
        requested or socket_session.get("room"), user_id, canvas_sessions
    )
    session = canvas_sessions.get(session_key)
    if not session:
        raise WebAppAuthError("crocodile session is not active")

    if bind_room:
        start_param = socket_session.get("start_param")
        if start_param:
            signed_room, _ = normalize_crocodile_room(start_param)
            if signed_room != canonical:
                raise WebAppAuthError("room does not match signed start_param")
        token = str(session.setdefault("_canvas_token", secrets.token_urlsafe(18)))
        socket_session["room"] = canonical
        socket_session["chat_id"] = session_key
        socket_session["crocodile_mode_token"] = token
        await crocodile.sio.save_session(sid, socket_session)
        return canonical, session_key, session

    if socket_session.get("room") != canonical:
        raise WebAppAuthError("socket attempted to switch rooms")
    if socket_session.get("crocodile_mode_token") != session.get("_canvas_token"):
        raise WebAppAuthError("socket belongs to an expired Crocodile canvas")
    return canonical, session_key, session


def canvas_join_payload(session: dict) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ui_mode": session.get("ui_mode", "draw"),
        "word": session.get("word", ""),
        "prompt": session.get("prompt", ""),
    }
    reference = session.get("reference_image")
    if isinstance(reference, (bytes, bytearray)) and reference:
        payload["reference_image"] = (
            "data:image/jpeg;base64," + base64.b64encode(bytes(reference)).decode("ascii")
        )
    return payload


async def snapshot_with_modes(sid, data, callback=None):
    try:
        _canonical, session_key = normalize_crocodile_room(data.get("room") if isinstance(data, dict) else None)
    except WebAppAuthError:
        return await _original_snapshot(sid, data, callback=callback)
    if ":" not in session_key:
        return await _original_snapshot(sid, data, callback=callback)

    try:
        _room, _key, session = await crocodile._authorize_socket_room(sid, data)
        encoded = str(data.get("image") or "").split(",", 1)[-1]
        image = base64.b64decode(encoded)
        if not image:
            raise ValueError("empty image")
        now = time.time()
        last = float(session.get("last_preview_time") or 0)
        if last and now - last < crocodile.PREVIEW_UPDATE_INTERVAL:
            result = "Skipped (Throttled)"
        else:
            session["last_preview_bytes"] = image
            session["last_preview_time"] = now
            preview_id = session.get("preview_message_id")
            if preview_id and not session.get("suppress_chat_preview"):
                await crocodile._safe_edit_media(
                    int(session["chat_id"]), int(preview_id), image,
                    f"🎨 *Рисует:* {session.get('drawer_name', 'Player')}",
                )
            result = "OK"
    except Exception as exc:
        logging.warning("[croc-mode] snapshot rejected: %s", exc)
        result = "Unauthorized" if isinstance(exc, WebAppAuthError) else "Error"
    if callback:
        await callback(result)
    return result


async def final_frame_with_modes(sid, data):
    try:
        _canonical, session_key = normalize_crocodile_room(data.get("room") if isinstance(data, dict) else None)
    except WebAppAuthError:
        return await _original_final_frame(sid, data)

    if ":" in session_key:
        try:
            _room, key, session = await crocodile._authorize_socket_room(sid, data)
            encoded = str(data.get("image") or "").split(",", 1)[-1]
            image = base64.b64decode(encoded)
        except Exception:
            logging.exception("[croc-mode] synthetic final frame failed auth/decode")
            return
        session["last_preview_bytes"] = image
        if session.get("mode") == "duel":
            await _finish_duel_canvas(key, session, image)
        elif session.get("mode") == "telephone":
            await _finish_telephone_canvas(key, session, image)
        return

    # Regular/duo round: keep legacy finish behaviour and archive the bitmap.
    session = crocodile.game_sessions.get(session_key)
    archive = None
    if session:
        try:
            image = base64.b64decode(str(data.get("image") or "").split(",", 1)[-1])
            archive = (int(session_key), image, str(session.get("word") or ""), session_artist_names(session), str(session.get("mode") or "classic"))
        except Exception:
            archive = None
    result = await _original_final_frame(sid, data)
    if archive:
        await record_drawing(*archive)
    return result


async def check_regular_answer_with_archive(message) -> bool:
    cid = str(message.chat.id)
    session = crocodile.game_sessions.get(cid)
    if session and message.text and is_session_drawer(
        session, message.from_user.id if message.from_user else None
    ) and crocodile._contains_answer(message.text, session.get("word", "")):
        return True

    archive = None
    if session and message.text and crocodile._contains_answer(message.text, session.get("word", "")):
        archive = (
            message.chat.id,
            session.get("last_preview_bytes"),
            str(session.get("word") or ""),
            session_artist_names(session),
            str(session.get("mode") or "classic"),
        )
    handled = await _original_check_answer(message)
    if handled and archive:
        await record_drawing(*archive)
    return handled


# ---------------- duel ----------------
def _duel_lobby_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Принять дуэль", callback_data=f"cduel_join_{chat_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"cduel_cancel_{chat_id}")],
        ]
    )


async def start_duel(message) -> None:
    cid = str(message.chat.id)
    if cid in duel_games or cid in telephone_games or cid in crocodile.game_sessions:
        await message.answer("Сначала закончите текущего кракадила.")
        return
    if not message.from_user:
        return
    duel_games[cid] = {
        "phase": "lobby",
        "artists": [(message.from_user.id, message.from_user.full_name)],
        "host_id": message.from_user.id,
        "votes": {},
    }
    await message.answer(
        f"⚔️ <b>{html.escape(message.from_user.full_name)}</b> вызывает чат на дуэль хуйдожников. Нужен второй смертник.",
        parse_mode="HTML",
        reply_markup=_duel_lobby_keyboard(message.chat.id),
    )


async def _start_duel_round(chat_id: str, duel: dict) -> None:
    word = crocodile._pick_word()
    duel.update({"phase": "drawing", "word": word, "started_at": time.time(), "finished": set()})
    blank = _blank()
    for slot, (user_id, name) in enumerate(duel["artists"], 1):
        suffix = f"d{slot}"
        key = _session_key(chat_id, suffix)
        preview = await bot.send_photo(
            int(chat_id),
            BufferedInputFile(blank, "blank.png"),
            caption=f"⚔️ Художник {slot}: {name}\n⏳ ждём мазню...",
            reply_markup=_canvas_button(chat_id, suffix, "🎨 Рисовать"),
        )
        canvas_sessions[key] = {
            "chat_id": str(chat_id), "drawer_id": user_id, "drawer_name": name,
            "word": word, "mode": "duel", "slot": slot, "ui_mode": "draw",
            "preview_message_id": preview.message_id, "last_preview_bytes": blank,
            "last_preview_time": 0, "duel_chat_id": str(chat_id),
        }
    await bot.send_message(
        int(chat_id),
        "⚔️ Оба видят <b>одно и то же слово</b> и рисуют параллельно. Остальные угадывают в чат; после ответа голосуем, чей шедевр менее преступный.",
        parse_mode="HTML",
    )


async def check_duel_answer(message) -> bool:
    cid = str(message.chat.id)
    duel = duel_games.get(cid)
    if not duel or duel.get("phase") != "drawing" or not message.text:
        return False
    artist_ids = {int(row[0]) for row in duel.get("artists", [])}
    if message.from_user and message.from_user.id in artist_ids:
        return False
    if not crocodile._contains_answer(message.text, duel.get("word", "")):
        return False
    if message.from_user:
        crocodile.add_point(cid, message.from_user.id, message.from_user.full_name)
    elapsed = max(0.0, time.time() - float(duel.get("started_at") or time.time()))
    try:
        from features.crocodile_scoring import record_artist_success
        for user_id, name in duel["artists"]:
            await record_artist_success(cid, user_id, name, elapsed_seconds=elapsed)
    except Exception:
        logging.exception("[croc-duel] failed to record artist timing")
    winner = message.from_user.full_name if message.from_user else "Кто-то"
    await bot.send_message(
        int(cid), f"🎉 <b>{html.escape(winner)}</b> угадал: <b>{html.escape(str(duel['word']))}</b>. Теперь судим художников.", parse_mode="HTML"
    )
    await _start_duel_vote(cid, duel)
    return True


async def _finish_duel_canvas(key: str, session: dict, image: bytes) -> None:
    cid = str(session["duel_chat_id"])
    duel = duel_games.get(cid)
    if not duel or duel.get("phase") != "drawing":
        return
    duel.setdefault("finished", set()).add(int(session["slot"]))
    await bot.send_message(int(cid), f"🏁 {session['drawer_name']} закончил свой рисунок.")
    if len(duel["finished"]) >= 2:
        await bot.send_message(int(cid), f"Оба закончили. Слово было: <b>{html.escape(str(duel['word']))}</b>.", parse_mode="HTML")
        await _start_duel_vote(cid, duel)


async def _start_duel_vote(chat_id: str, duel: dict) -> None:
    if duel.get("phase") == "voting":
        return
    duel["phase"] = "voting"
    duel["votes"] = {}
    images: dict[int, bytes] = {}
    for slot in (1, 2):
        key = _session_key(chat_id, f"d{slot}")
        session = canvas_sessions.pop(key, None)
        if not session:
            continue
        image = session.get("last_preview_bytes") or _blank()
        images[slot] = image
        try:
            await crocodile.sio.close_room(_chat_room(chat_id, f"d{slot}"))
        except Exception:
            pass
        await bot.send_photo(
            int(chat_id), BufferedInputFile(image, f"duel-{slot}.jpg"),
            caption=f"🎨 Вариант {slot}: {session['drawer_name']}",
        )
        await record_drawing(chat_id, image, duel.get("word", ""), [session["drawer_name"]], "duel")
    duel["images"] = images
    await bot.send_message(
        int(chat_id),
        "🗳 <b>Какой рисунок лучше?</b> На издевательства — 60 секунд.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="1️⃣ Первый", callback_data=f"cduel_vote1_{chat_id}"), InlineKeyboardButton(text="2️⃣ Второй", callback_data=f"cduel_vote2_{chat_id}")]]
        ),
    )
    duel["vote_task"] = crocodile._start_background_task(
        _duel_vote_timer(chat_id, duel), name=f"crocodile-duel-vote:{chat_id}"
    )


async def _duel_vote_timer(chat_id: str, duel: dict) -> None:
    try:
        await asyncio.sleep(DUEL_VOTE_SECONDS)
        await _finish_duel_vote(chat_id, duel)
    except asyncio.CancelledError:
        return


async def _finish_duel_vote(chat_id: str, duel: dict) -> None:
    if duel_games.get(chat_id) is not duel:
        return
    duel_games.pop(chat_id, None)
    counts = {1: 0, 2: 0}
    for slot in duel.get("votes", {}).values():
        if slot in counts:
            counts[slot] += 1
    artists = duel.get("artists", [])
    if counts[1] == counts[2]:
        text = f"🤝 Ничья {counts[1]}:{counts[2]}. Оба одинаково опасны для изобразительного искусства."
    else:
        slot = 1 if counts[1] > counts[2] else 2
        name = artists[slot - 1][1]
        text = f"🏆 Победил <b>{html.escape(name)}</b> — {counts[slot]} голос(а)."
    await bot.send_message(int(chat_id), text, parse_mode="HTML")


async def handle_duel_callback(callback) -> None:
    data = callback.data or ""
    if data.startswith("cduel_join_"):
        cid = data[len("cduel_join_"):]
        duel = duel_games.get(cid)
        if not duel or duel.get("phase") != "lobby":
            return await callback.answer("Дуэль уже уехала без тебя")
        if callback.from_user.id == duel["host_id"]:
            return await callback.answer("Сам с собой? Это уже перформанс.", show_alert=True)
        duel["artists"].append((callback.from_user.id, callback.from_user.full_name))
        await callback.answer("Добро пожаловать на мольберт")
        await _start_duel_round(cid, duel)
        return
    if data.startswith("cduel_cancel_"):
        cid = data[len("cduel_cancel_"):]
        duel = duel_games.get(cid)
        if duel and callback.from_user.id == duel.get("host_id"):
            duel_games.pop(cid, None)
            await callback.answer("Отменено")
            await callback.message.answer("⚔️ Дуэль отменена. Искусство спасено.")
        else:
            await callback.answer("Отменить может только инициатор", show_alert=True)
        return
    if data.startswith("cduel_vote"):
        rest = data[len("cduel_vote"):]
        slot_text, _, cid = rest.partition("_")
        duel = duel_games.get(cid)
        if not duel or duel.get("phase") != "voting":
            return await callback.answer("Голосование уже закончилось")
        artist_ids = {int(row[0]) for row in duel.get("artists", [])}
        if callback.from_user.id in artist_ids:
            return await callback.answer("Художники за себя не голосуют 😏", show_alert=True)
        if callback.from_user.id in duel["votes"]:
            return await callback.answer("Ты уже проголосовал")
        slot = int(slot_text)
        duel["votes"][callback.from_user.id] = slot
        await callback.answer("Голос принят")


# ---------------- telephone ----------------
def _telephone_lobby_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Участвовать", callback_data=f"ctel_join_{chat_id}")],
            [InlineKeyboardButton(text="▶️ Начать", callback_data=f"ctel_start_{chat_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"ctel_cancel_{chat_id}")],
        ]
    )


async def start_telephone(message) -> None:
    cid = str(message.chat.id)
    if cid in telephone_games or cid in duel_games or cid in crocodile.game_sessions:
        await message.answer("Сначала закончите текущего кракадила.")
        return
    if not message.from_user:
        return
    telephone_games[cid] = {
        "phase": "lobby", "host_id": message.from_user.id,
        "players": [(message.from_user.id, message.from_user.full_name)], "chain": [], "step": 0,
    }
    await message.answer(
        f"☎️ <b>ИСПОРЧЕННЫЙ КРАКАДИЛ</b>\n{html.escape(message.from_user.full_name)} уже в цепочке. Нужно минимум {TELEPHONE_MIN_PLAYERS}, максимум {TELEPHONE_MAX_PLAYERS}.\n\nКаждый увидит только предыдущий шаг: слово → рисунок → догадка → рисунок…",
        parse_mode="HTML", reply_markup=_telephone_lobby_keyboard(message.chat.id),
    )


async def _send_telephone_step(chat_id: str, game: dict) -> None:
    step = int(game["step"])
    players = game["players"]
    if step >= len(players):
        await _finish_telephone(chat_id, game)
        return
    user_id, name = players[step]
    suffix = f"t{step}"
    key = _session_key(chat_id, suffix)
    if step == 0:
        ui_mode = "text"
        prompt = "Загадай слово или короткую фразу. Следующий игрок увидит только её и будет рисовать."
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

    canvas_sessions[key] = {
        "chat_id": str(chat_id), "drawer_id": user_id, "drawer_name": name,
        "mode": "telephone", "telephone_chat_id": str(chat_id), "telephone_step": step,
        "ui_mode": ui_mode, "word": word, "prompt": prompt, "reference_image": reference,
        "suppress_chat_preview": True, "last_preview_bytes": _blank(), "last_preview_time": 0,
    }
    action = "рисуй" if ui_mode == "draw" else ("загадывай" if step == 0 else "угадывай")
    await bot.send_message(
        int(chat_id),
        f"☎️ Ход <b>{html.escape(name)}</b>: {action}. Остальные не подглядывают.",
        parse_mode="HTML",
        reply_markup=_canvas_button(chat_id, suffix, "✍️ Открыть свой ход"),
    )


async def _advance_telephone(chat_id: str, game: dict) -> None:
    old_step = int(game["step"])
    old_key = _session_key(chat_id, f"t{old_step}")
    canvas_sessions.pop(old_key, None)
    try:
        await crocodile.sio.close_room(_chat_room(chat_id, f"t{old_step}"))
    except Exception:
        pass
    game["step"] = old_step + 1
    await _send_telephone_step(chat_id, game)


async def submit_telephone_text(sid, data):
    try:
        _room, key, session = await crocodile._authorize_socket_room(sid, data)
    except WebAppAuthError:
        return {"ok": False, "error": "unauthorized"}
    if session.get("mode") != "telephone" or session.get("ui_mode") != "text":
        return {"ok": False, "error": "wrong mode"}
    value = " ".join(str(data.get("text") or "").split()).strip()
    if not 1 <= len(value) <= 120:
        return {"ok": False, "error": "Введите от 1 до 120 символов"}
    cid = str(session["telephone_chat_id"])
    game = telephone_games.get(cid)
    if not game or int(game.get("step", -1)) != int(session["telephone_step"]):
        return {"ok": False, "error": "step expired"}
    game["chain"].append({
        "kind": "text", "value": value, "user_id": session["drawer_id"], "user_name": session["drawer_name"],
    })
    await _advance_telephone(cid, game)
    return {"ok": True}


async def _finish_telephone_canvas(key: str, session: dict, image: bytes) -> None:
    cid = str(session["telephone_chat_id"])
    game = telephone_games.get(cid)
    if not game or int(game.get("step", -1)) != int(session["telephone_step"]):
        return
    prompt = str(session.get("word") or "")
    game["chain"].append({
        "kind": "image", "image": image, "value": prompt,
        "user_id": session["drawer_id"], "user_name": session["drawer_name"],
    })
    await record_drawing(cid, image, prompt, [session["drawer_name"]], "telephone")
    await _advance_telephone(cid, game)


async def _finish_telephone(chat_id: str, game: dict) -> None:
    if telephone_games.get(chat_id) is not game:
        return
    telephone_games.pop(chat_id, None)
    await bot.send_message(int(chat_id), "☎️ <b>ЦЕПОЧКА ИСПОРЧЕНА. СМОТРИМ, ГДЕ ВСЁ ПОШЛО НЕ ТАК:</b>", parse_mode="HTML")
    for index, item in enumerate(game.get("chain", []), 1):
        name = html.escape(str(item.get("user_name") or "Игрок"))
        if item.get("kind") == "image":
            await bot.send_photo(
                int(chat_id), BufferedInputFile(item["image"], f"telephone-{index}.jpg"),
                caption=f"{index}. 🎨 {name} нарисовал это по версии: «{item.get('value', '')}»",
            )
        else:
            label = "загадал" if index == 1 else "решил, что это"
            await bot.send_message(int(chat_id), f"{index}. ✍️ <b>{name}</b> {label}: <b>{html.escape(str(item.get('value') or ''))}</b>", parse_mode="HTML")
    await bot.send_message(int(chat_id), "🏁 Телефон окончательно испорчен. Можно начинать новый.")


async def handle_telephone_callback(callback) -> None:
    data = callback.data or ""
    for prefix in ("ctel_join_", "ctel_start_", "ctel_cancel_"):
        if data.startswith(prefix):
            cid = data[len(prefix):]
            break
    else:
        return
    game = telephone_games.get(cid)
    if not game or game.get("phase") != "lobby":
        return await callback.answer("Эта цепочка уже закрыта")
    if data.startswith("ctel_join_"):
        ids = {int(row[0]) for row in game["players"]}
        if callback.from_user.id in ids:
            return await callback.answer("Ты уже в цепочке")
        if len(game["players"]) >= TELEPHONE_MAX_PLAYERS:
            return await callback.answer("Мест больше нет", show_alert=True)
        game["players"].append((callback.from_user.id, callback.from_user.full_name))
        await callback.answer(f"Ты участник №{len(game['players'])}")
        await callback.message.answer(f"☎️ В цепочку влез {callback.from_user.full_name}. Теперь вас {len(game['players'])}.")
        return
    if callback.from_user.id != game["host_id"]:
        return await callback.answer("Это может сделать только ведущий", show_alert=True)
    if data.startswith("ctel_cancel_"):
        telephone_games.pop(cid, None)
        await callback.answer("Отменено")
        await callback.message.answer("☎️ Телефон положили обратно на рычаг.")
        return
    if len(game["players"]) < TELEPHONE_MIN_PLAYERS:
        return await callback.answer(f"Нужно минимум {TELEPHONE_MIN_PLAYERS} человека", show_alert=True)
    game["phase"] = "playing"
    await callback.answer("Поехали")
    await _send_telephone_step(cid, game)


def configure_crocodile_modes() -> None:
    """Install mode extensions after persistence/controls and before canvas restore."""
    global _configured, _original_authorize_socket_room, _original_snapshot, _original_final_frame
    global _original_get_game_keyboard, _original_handle_callback, _original_check_answer
    if _configured:
        return
    _original_authorize_socket_room = crocodile._authorize_socket_room
    _original_snapshot = crocodile.snapshot
    _original_final_frame = crocodile.final_frame
    _original_get_game_keyboard = crocodile.get_game_keyboard
    _original_handle_callback = crocodile.handle_callback
    _original_check_answer = crocodile.check_answer

    crocodile._authorize_socket_room = _authorize_socket_room_with_modes
    crocodile.get_game_keyboard = get_game_keyboard_with_duo
    crocodile.handle_callback = handle_regular_callback
    crocodile.check_answer = check_regular_answer_with_archive
    crocodile.sio.on("snapshot", handler=snapshot_with_modes)
    crocodile.sio.on("final_frame", handler=final_frame_with_modes)
    crocodile.sio.on("submit_text", handler=submit_telephone_text)
    _configured = True
