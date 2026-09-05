# dnd.py

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import Message, PollAnswer

from core.paths import DND_STATE_PATH
from core.state import chat_settings
from infrastructure.ai.clients import gigachat_model, groq_ai, model


dnd_router = Router()

# Хранилище активных сессий: chat_id -> GameSession
dnd_sessions = {}
# Хранилище связи опроса с чатом: poll_id -> chat_id (нужно для PollAnswer)
poll_map = {}

DND_MODEL_TIMEOUT_SECONDS = 90
DND_POLL_TIMEOUT_SECONDS = 300
DND_ACTION_WINDOW_SECONDS = 30
DND_RECENT_SCENE_LIMIT = 2
DND_SCENE_TYPES = (
    "исследование",
    "социальная сцена",
    "опасность",
    "загадка",
    "погоня",
    "находка",
    "конфликт",
    "сюжетный поворот",
)

_task_supervisor = None
_finalizing_polls = set()


DND_SYSTEM_PROMPT = """
Ты — Мастер Подземелий (Dungeon Master) в текстовой RPG.
Твой характер: Ироничный, дерзкий, саркастичный, грубый. Ты используешь сленг, нецензурную лексику.
Иногда злись на играющих.

Твоя задача:
1. Генерировать ОЧЕНЬ КОРОТКИЕ куски сюжета (СТРОГО до 100 слов). Не лей воду.
2. В конце сообщения ОБЯЗАТЕЛЬНО укажи один из технических тегов действий.
3. Когда в запросе есть строка «РЕЖИССЁР СЦЕНЫ», используй указанный тип как доминирующий
   характер ближайшего сюжетного эпизода. Не называй тип сцены игрокам и не ломай причинность
   ради него: это творческое ограничение, а не команда резко телепортировать сюжет.
4. Не зацикливайся на одинаковой структуре ходов: чередуй способы подачи, конфликты,
   взаимодействие с окружением и последствия действий игроков.

ФОРМАТ ТЕХНИЧЕСКИХ ТЕГОВ (В конце сообщения):

Если нужна развилка сюжета (Опрос):
[ACTION:POLL;OPTIONS:Вариант 1;Вариант 2;Вариант 3]
(Максимум 4 варианта).

Если нужна проверка навыка (Бросок кубика):
[ACTION:ROLL;STAT:Название характеристики]

Если нужен свободный групповой ход игроков:
[ACTION:INPUT]

Если игрок попросил завершить игру, опиши гибель и закончи тегом:
[ACTION:END]
"""


def configure_task_supervisor(supervisor):
    """Передать владельца динамических DnD-задач из composition root."""
    global _task_supervisor
    _task_supervisor = supervisor


def _start_background_task(coro, *, name: str):
    if _task_supervisor is None:
        coro.close()
        raise RuntimeError("DnD task supervisor is not configured")
    return _task_supervisor.start(coro, name=name)


def get_active_model(chat_id):
    """Возвращает активную модель для DND на основе настроек чата."""
    settings = chat_settings.get(str(chat_id), {})
    active_model = settings.get("active_model", "gemini")
    if active_model == "history":
        active_model = "gemini"
    return active_model


class GameSession:
    def __init__(
        self,
        chat_id,
        starter_name=None,
        *,
        active_model=None,
        conversation=None,
    ):
        self.chat_id = chat_id
        self.active_model = active_model or get_active_model(chat_id)
        self.state = "WAITING_BACKSTORY"
        self.last_roll_stat = None
        self.current_poll_id = None
        self.pending_poll = None

        # Групповой ход: игроки отвечают реплаями на action_prompt_message_id.
        self.action_prompt_message_id = None
        self.pending_actions = {}
        self.action_deadline = None

        # Последние типы сцен нужны, чтобы режиссёр не повторялся подряд.
        self.recent_scene_types = []

        if conversation is None:
            starter = starter_name or "игрок"
            self.conversation = [
                {
                    "role": "user",
                    "content": (
                        DND_SYSTEM_PROMPT
                        + "\n\n"
                        + f"Начинай игру. Инициатор: {starter}. Помни: не более 100 слов."
                    ),
                },
                {"role": "assistant", "content": "Погнали."},
            ]
        else:
            self.conversation = [dict(item) for item in conversation]

        self.chat_session = None
        if self.active_model == "gemini":
            history = [
                {
                    "role": "model" if item["role"] == "assistant" else "user",
                    "parts": [item["content"]],
                }
                for item in self.conversation
            ]
            self.chat_session = model.start_chat(chat_id=chat_id, history=history)

    def send_message(self, message_text):
        """Отправить сообщение провайдеру и сохранить переносимый контекст."""
        if self.active_model == "gemini":
            response = self.chat_session.send_message(message_text, chat_id=self.chat_id)
            result = response.text
        elif self.active_model == "gigachat":
            history = self.conversation + [{"role": "user", "content": message_text}]
            full_prompt = "\n".join(
                f"{item['role']}: {item['content']}" for item in history
            )
            response = gigachat_model.generate_content(full_prompt, chat_id=self.chat_id)
            result = response.text
        elif self.active_model == "groq":
            history = self.conversation + [{"role": "user", "content": message_text}]
            full_prompt = "\n".join(
                f"{item['role']}: {item['content']}" for item in history
            )
            result = groq_ai.generate_text(full_prompt, max_tokens=512)
        else:
            raise RuntimeError(f"Unsupported DnD model: {self.active_model}")

        self.conversation.append({"role": "user", "content": message_text})
        self.conversation.append({"role": "assistant", "content": result})
        return result

    def to_record(self):
        return {
            "chat_id": self.chat_id,
            "active_model": self.active_model,
            "conversation": self.conversation,
            "state": self.state,
            "last_roll_stat": self.last_roll_stat,
            "current_poll_id": self.current_poll_id,
            "pending_poll": self.pending_poll,
            "action_prompt_message_id": self.action_prompt_message_id,
            "pending_actions": self.pending_actions,
            "action_deadline": self.action_deadline,
            "recent_scene_types": self.recent_scene_types,
        }

    @classmethod
    def from_record(cls, record):
        session = cls(
            int(record["chat_id"]),
            active_model=record.get("active_model") or "gemini",
            conversation=record.get("conversation") or None,
        )
        session.state = record.get("state") or "WAITING_ACTION"
        session.last_roll_stat = record.get("last_roll_stat")
        session.current_poll_id = record.get("current_poll_id")
        session.pending_poll = record.get("pending_poll")
        session.action_prompt_message_id = record.get("action_prompt_message_id")
        session.pending_actions = dict(record.get("pending_actions") or {})
        session.action_deadline = record.get("action_deadline")
        session.recent_scene_types = list(record.get("recent_scene_types") or [])[
            -DND_RECENT_SCENE_LIMIT:
        ]
        return session


def _state_path() -> Path:
    return Path(DND_STATE_PATH)


def persist_dnd_sessions() -> None:
    """Атомарно сохранить активные DnD-сессии на диск."""
    path = _state_path()
    payload = {
        "version": 1,
        "sessions": [session.to_record() for session in dnd_sessions.values()],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def choose_next_scene_type(session: GameSession) -> str:
    """Выбрать следующий тип сцены, исключив несколько последних."""
    recent = set(session.recent_scene_types[-DND_RECENT_SCENE_LIMIT:])
    candidates = [scene for scene in DND_SCENE_TYPES if scene not in recent]
    if not candidates:
        candidates = list(DND_SCENE_TYPES)

    scene_type = random.choice(candidates)
    session.recent_scene_types.append(scene_type)
    session.recent_scene_types = session.recent_scene_types[-DND_RECENT_SCENE_LIMIT:]
    return scene_type


def with_scene_direction(session: GameSession, prompt: str) -> str:
    """Добавить модели мягкое режиссёрское ограничение для разнообразия сцен."""
    scene_type = choose_next_scene_type(session)
    return (
        f"{prompt}\n\n"
        f"РЕЖИССЁР СЦЕНЫ: {scene_type}. "
        "Сделай этот тип доминирующим в ближайшем сюжетном эпизоде, "
        "не называй его игрокам и не ломай текущую причинность."
    )


def _action_prompt_text() -> str:
    return (
        "🎭 Ход партии. Пишите действия реплаями на это сообщение.\n"
        f"После первого действия собираю остальные ещё {DND_ACTION_WINDOW_SECONDS} секунд. "
        "Каждый игрок может переписать своё действие новым реплаем."
    )


async def open_action_window(bot: Bot, chat_id: int):
    """Открыть новый групповой ход без таймера до первого действия."""
    session = dnd_sessions.get(chat_id)
    if not session:
        return None

    session.state = "WAITING_ACTION"
    session.pending_actions = {}
    session.action_deadline = None
    session.action_prompt_message_id = None
    persist_dnd_sessions()

    prompt_message = await bot.send_message(chat_id, _action_prompt_text())
    session.action_prompt_message_id = prompt_message.message_id
    persist_dnd_sessions()
    logging.info(
        "DnD action window opened chat_id=%s prompt_message_id=%s",
        chat_id,
        prompt_message.message_id,
    )
    return prompt_message


async def _restore_action_prompt(bot: Bot, chat_id: int):
    """Для старого persisted state создать отсутствующий prompt группового хода."""
    session = dnd_sessions.get(chat_id)
    if not session or session.state != "WAITING_ACTION":
        return
    if getattr(session, "action_prompt_message_id", None):
        return
    await open_action_window(bot, chat_id)


def restore_dnd_sessions(bot: Bot) -> int:
    """Восстановить сессии и таймеры после рестарта процесса."""
    path = _state_path()
    if not path.exists():
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("DnD state restore failed path=%s", path)
        return 0

    restored = 0
    dnd_sessions.clear()
    poll_map.clear()

    for record in payload.get("sessions", []):
        try:
            session = GameSession.from_record(record)
            dnd_sessions[session.chat_id] = session
            restored += 1

            poll = session.pending_poll
            if session.state == "WAITING_POLL" and session.current_poll_id and poll:
                poll_id = str(session.current_poll_id)
                poll_map[poll_id] = session.chat_id
                deadline = float(poll.get("deadline", time.time()))
                delay = max(0.0, deadline - time.time())
                _start_background_task(
                    wait_for_poll_timeout(
                        bot,
                        session.chat_id,
                        int(poll.get("poll_chat_id", session.chat_id)),
                        int(poll["message_id"]),
                        list(poll["options"]),
                        poll_id,
                        delay_seconds=delay,
                    ),
                    name=f"dnd-poll:{session.chat_id}:{poll_id}:restored",
                )
                logging.info(
                    "DnD poll restored chat_id=%s poll_id=%s remaining=%.1fs",
                    session.chat_id,
                    poll_id,
                    delay,
                )
            elif session.state == "WAITING_POLL":
                session.state = "WAITING_ACTION"
                session.current_poll_id = None
                session.pending_poll = None

            if session.state == "WAITING_ACTION":
                prompt_id = getattr(session, "action_prompt_message_id", None)
                action_deadline = getattr(session, "action_deadline", None)
                pending_actions = getattr(session, "pending_actions", {}) or {}
                if prompt_id and action_deadline is not None and pending_actions:
                    delay = max(0.0, float(action_deadline) - time.time())
                    _start_background_task(
                        wait_for_action_timeout(
                            bot,
                            session.chat_id,
                            int(prompt_id),
                            delay_seconds=delay,
                        ),
                        name=f"dnd-actions:{session.chat_id}:{prompt_id}:restored",
                    )
                    logging.info(
                        "DnD action timer restored chat_id=%s prompt_message_id=%s remaining=%.1fs",
                        session.chat_id,
                        prompt_id,
                        delay,
                    )
                elif not prompt_id:
                    _start_background_task(
                        _restore_action_prompt(bot, session.chat_id),
                        name=f"dnd-actions:{session.chat_id}:restore-prompt",
                    )
        except Exception:
            logging.exception("DnD session restore failed record=%r", record)

    if restored:
        persist_dnd_sessions()
        logging.info("DnD restored sessions=%s", restored)
    return restored


async def create_game_session(chat_id: int, starter_name: str):
    """Создать provider-backed сессию вне event loop с ограничением времени."""
    return await asyncio.wait_for(
        asyncio.to_thread(GameSession, chat_id, starter_name),
        timeout=DND_MODEL_TIMEOUT_SECONDS,
    )


async def generate_session_response(session: GameSession, prompt: str) -> str:
    """Вызвать provider wrapper вне event loop и сохранить обновлённый контекст."""
    result = await asyncio.wait_for(
        asyncio.to_thread(session.send_message, prompt),
        timeout=DND_MODEL_TIMEOUT_SECONDS,
    )
    if dnd_sessions.get(session.chat_id) is session:
        persist_dnd_sessions()
    return result


async def parse_and_execute_turn(bot: Bot, chat_id: int, text_response: str):
    session = dnd_sessions.get(chat_id)
    if not session:
        return

    action_match = re.search(r"\[ACTION:(.*?)\]", text_response)
    clean_text = re.sub(r"\[ACTION:.*?\]", "", text_response).strip()

    if clean_text:
        await bot.send_message(chat_id, clean_text)

    if not action_match:
        await open_action_window(bot, chat_id)
        return

    command_str = action_match.group(1)

    if command_str.startswith("POLL"):
        try:
            options_part = command_str.split("OPTIONS:", 1)[1]
            options = [opt.strip() for opt in options_part.split(";")]
            options = [option for option in options if option][:4]
            if len(options) < 2:
                raise ValueError("DnD poll needs at least two options")

            session.state = "WAITING_POLL"
            session.action_prompt_message_id = None
            session.pending_actions = {}
            session.action_deadline = None
            poll_msg = await bot.send_poll(
                chat_id=chat_id,
                question="Чё делать будем?",
                options=options,
                is_anonymous=False,
            )

            poll_id = str(poll_msg.poll.id)
            deadline = time.time() + DND_POLL_TIMEOUT_SECONDS
            session.current_poll_id = poll_id
            session.pending_poll = {
                "poll_id": poll_id,
                "poll_chat_id": poll_msg.chat.id,
                "message_id": poll_msg.message_id,
                "options": options,
                "deadline": deadline,
            }
            poll_map[poll_id] = chat_id
            persist_dnd_sessions()

            _start_background_task(
                wait_for_poll_timeout(
                    bot,
                    chat_id,
                    poll_msg.chat.id,
                    poll_msg.message_id,
                    options,
                    poll_id,
                ),
                name=f"dnd-poll:{chat_id}:{poll_id}",
            )
            logging.info(
                "DnD poll started chat_id=%s poll_id=%s deadline=%s",
                chat_id,
                poll_id,
                deadline,
            )
        except Exception:
            logging.exception("DnD poll setup failed chat_id=%s", chat_id)
            session.current_poll_id = None
            session.pending_poll = None
            await bot.send_message(chat_id, "(Опрос развалился. Решайте словами).")
            await open_action_window(bot, chat_id)

    elif command_str.startswith("ROLL"):
        stat = command_str.split("STAT:", 1)[1].strip()
        session.last_roll_stat = stat
        session.state = "WAITING_ROLL"
        session.action_prompt_message_id = None
        session.pending_actions = {}
        session.action_deadline = None
        persist_dnd_sessions()
        await bot.send_message(
            chat_id,
            f"🎲 Проверка: *{stat}*. Пиши *кидаю*.",
            parse_mode="Markdown",
        )

    elif command_str.startswith("INPUT"):
        await open_action_window(bot, chat_id)

    elif command_str.startswith("END"):
        cleanup_session(chat_id)
        await bot.send_message(chat_id, "☠️ Игра окончена.")


def cleanup_session(chat_id):
    """Удалить сессию и её poll mapping, затем сохранить состояние."""
    session = dnd_sessions.pop(chat_id, None)
    if session and session.current_poll_id:
        poll_map.pop(str(session.current_poll_id), None)
    persist_dnd_sessions()
    logging.info("DnD session cleaned chat_id=%s", chat_id)


async def finalize_poll(bot: Bot, chat_id: int, message_id: int, options: list):
    """Остановить опрос, посчитать голоса и продолжить историю."""
    session = dnd_sessions.get(chat_id)
    if not session:
        return

    poll_id = str(session.current_poll_id or "")
    if not poll_id or poll_id in _finalizing_polls:
        return

    _finalizing_polls.add(poll_id)
    try:
        try:
            poll_res = await bot.stop_poll(chat_id=chat_id, message_id=message_id)
            max_votes = 0
            winners = []
            for option in poll_res.options:
                if option.voter_count > max_votes:
                    max_votes = option.voter_count
                    winners = [option.text]
                elif option.voter_count == max_votes and max_votes > 0:
                    winners.append(option.text)

            if winners:
                outcome = f"Выбор сделан: {random.choice(winners)}"
            else:
                outcome = f"Тишина... Случайность выбрала: {random.choice(options)}"
        except Exception:
            logging.exception(
                "DnD stop_poll failed chat_id=%s poll_id=%s; using fallback",
                chat_id,
                poll_id,
            )
            outcome = f"Опрос потерялся, судьба выбрала: {random.choice(options)}"

        poll_map.pop(poll_id, None)
        session.current_poll_id = None
        session.pending_poll = None
        session.state = "RESOLVING"
        persist_dnd_sessions()

        await bot.send_message(chat_id, f"✅ {outcome}")
        logging.info(
            "DnD poll finalized chat_id=%s poll_id=%s outcome=%s",
            chat_id,
            poll_id,
            outcome,
        )

        try:
            response_text = await generate_session_response(
                session,
                with_scene_direction(
                    session,
                    f"Результат: {outcome}. Продолжай (до 100 слов).",
                ),
            )
            await parse_and_execute_turn(bot, chat_id, response_text)
        except Exception:
            logging.exception("DnD continuation failed chat_id=%s", chat_id)
            await bot.send_message(
                chat_id,
                "Мастер на секунду выпал из реальности. История сохранена.",
            )
            await open_action_window(bot, chat_id)
    finally:
        _finalizing_polls.discard(poll_id)


async def wait_for_poll_timeout(
    bot: Bot,
    chat_id: int,
    poll_chat_id: int,
    message_id: int,
    options: list,
    poll_id: str,
    *,
    delay_seconds: float | None = None,
):
    """Дождаться дедлайна текущего poll и продолжить историю."""
    del poll_chat_id  # chat_id остаётся каноническим идентификатором сессии.
    delay = DND_POLL_TIMEOUT_SECONDS if delay_seconds is None else max(0.0, delay_seconds)
    await asyncio.sleep(delay)

    session = dnd_sessions.get(chat_id)
    if not session or str(session.current_poll_id) != str(poll_id):
        return

    await finalize_poll(bot, chat_id, message_id, options)


def _format_group_actions(actions: list[dict]) -> str:
    lines = []
    for item in actions:
        name = item.get("name") or "Игрок"
        action = item.get("action") or ""
        lines.append(f"- {name}: {action}")
    return "\n".join(lines)


async def finalize_group_actions(bot: Bot, chat_id: int, prompt_message_id: int):
    """Заморозить собранные действия и разрешить их одним ходом Мастера."""
    session = dnd_sessions.get(chat_id)
    if not session or session.state != "WAITING_ACTION":
        return
    if int(session.action_prompt_message_id or 0) != int(prompt_message_id):
        return

    actions = list((session.pending_actions or {}).values())
    session.action_deadline = None
    if not actions:
        persist_dnd_sessions()
        return

    session.state = "RESOLVING"
    session.action_prompt_message_id = None
    session.pending_actions = {}
    persist_dnd_sessions()

    actions_text = _format_group_actions(actions)
    await bot.send_message(chat_id, f"🎭 Ход партии:\n{actions_text}")

    try:
        response_text = await generate_session_response(
            session,
            with_scene_direction(
                session,
                (
                    "Игроки заявили действия одновременно:\n"
                    f"{actions_text}\n"
                    "Разреши их в одной общей сцене: учти взаимодействие действий, "
                    "противоречия и последствия. Продолжай до 100 слов."
                ),
            ),
        )
        await parse_and_execute_turn(bot, chat_id, response_text)
    except Exception:
        logging.exception("DnD group action continuation failed chat_id=%s", chat_id)
        await bot.send_message(
            chat_id,
            "Мастер завис на коллективном безумии. Повторите действия.",
        )
        await open_action_window(bot, chat_id)


async def wait_for_action_timeout(
    bot: Bot,
    chat_id: int,
    prompt_message_id: int,
    *,
    delay_seconds: float | None = None,
):
    """После первого действия дать остальным короткое окно и закрыть групповой ход."""
    delay = DND_ACTION_WINDOW_SECONDS if delay_seconds is None else max(0.0, delay_seconds)
    await asyncio.sleep(delay)

    session = dnd_sessions.get(chat_id)
    if not session or session.state != "WAITING_ACTION":
        return
    if int(session.action_prompt_message_id or 0) != int(prompt_message_id):
        return

    await finalize_group_actions(bot, chat_id, prompt_message_id)


@dnd_router.poll_answer(lambda event: event.poll_id in poll_map)
async def handle_poll_answer(poll_answer: PollAnswer, bot: Bot):
    del bot
    chat_id = poll_map.get(poll_answer.poll_id)
    if chat_id:
        logging.info(
            "DnD poll vote chat_id=%s poll_id=%s user_id=%s",
            chat_id,
            poll_answer.poll_id,
            poll_answer.user.id,
        )


@dnd_router.message(lambda m: m.text and m.text.lower().startswith("упупа начни историю"))
async def cmd_start_dnd(message: Message):
    user_name = message.from_user.first_name
    cleanup_session(message.chat.id)
    try:
        session = await create_game_session(message.chat.id, user_name)
        dnd_sessions[message.chat.id] = session
        persist_dnd_sessions()
    except asyncio.TimeoutError:
        await message.answer("Мастер завис в астрале. Попробуй начать историю ещё раз.")
        return
    except Exception:
        logging.exception("DnD session creation failed chat_id=%s", message.chat.id)
        await message.answer("Не удалось разбудить мастера историй.")
        return

    logging.info(
        "DnD session started chat_id=%s model=%s",
        message.chat.id,
        session.active_model,
    )
    await message.answer(f"Ладно, {user_name}. Какую предысторию хочешь? (Ответь реплаем)")


@dnd_router.message(F.text.lower().startswith(("упупа заверши историю", "упупа закончи историю")))
async def cmd_stop_dnd(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session:
        await message.answer("Мы и не играем.")
        return

    try:
        response_text = await generate_session_response(
            session,
            "Игроки хотят конец игры. Опиши короткий финал с тегом [ACTION:END]",
        )
        await parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD ending failed chat_id=%s", message.chat.id)
        cleanup_session(message.chat.id)
        await message.answer("Игра окончена.")


@dnd_router.message(
    lambda m: m.reply_to_message
    and dnd_sessions.get(m.chat.id)
    and dnd_sessions[m.chat.id].state == "WAITING_BACKSTORY"
)
async def handle_backstory(message: Message):
    session = dnd_sessions[message.chat.id]
    backstory = message.text or message.caption
    if not backstory:
        await message.answer("Предысторию лучше прислать текстом.")
        return

    msg = await message.answer("Генерирую...")
    try:
        response_text = await generate_session_response(
            session,
            with_scene_direction(
                session,
                f"Предыстория: {backstory}. Начинай.",
            ),
        )
        try:
            await message.bot.delete_message(message.chat.id, msg.message_id)
        except Exception:
            pass
        await parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD backstory generation failed chat_id=%s", message.chat.id)
        await message.answer("Мастер завис, но история сохранена. Попробуй ещё раз реплаем.")


@dnd_router.message(F.text.lower().contains("кидаю"))
async def handle_roll(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session or session.state != "WAITING_ROLL":
        return

    roll_result = random.randint(1, 20)
    stat = session.last_roll_stat
    await message.answer(
        f"🎲 {message.from_user.first_name}: {stat} -> **{roll_result}**",
        parse_mode="Markdown",
    )

    try:
        response_text = await generate_session_response(
            session,
            with_scene_direction(
                session,
                f"Игрок кинул на {stat}: {roll_result}. Продолжай.",
            ),
        )
        await parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD roll continuation failed chat_id=%s", message.chat.id)
        await message.answer("Мастер завис, но история сохранена.")
        await open_action_window(message.bot, message.chat.id)


@dnd_router.message(
    lambda m: dnd_sessions.get(m.chat.id)
    and dnd_sessions[m.chat.id].state == "WAITING_ACTION"
)
async def handle_free_action(message: Message):
    """Собирать только реплаи на текущий prompt и разрешать их одним общим ходом."""
    session = dnd_sessions[message.chat.id]
    prompt_message_id = session.action_prompt_message_id
    if not prompt_message_id or not message.reply_to_message:
        return
    if int(message.reply_to_message.message_id) != int(prompt_message_id):
        return

    user_action = message.text or message.caption
    if not user_action or user_action.lower().startswith("упупа"):
        return

    user_id = int(message.from_user.id)
    user_name = message.from_user.first_name
    session.pending_actions[str(user_id)] = {
        "user_id": user_id,
        "name": user_name,
        "action": user_action,
    }

    first_action = session.action_deadline is None
    if first_action:
        session.action_deadline = time.time() + DND_ACTION_WINDOW_SECONDS
    persist_dnd_sessions()

    if first_action:
        _start_background_task(
            wait_for_action_timeout(
                message.bot,
                message.chat.id,
                int(prompt_message_id),
            ),
            name=f"dnd-actions:{message.chat.id}:{prompt_message_id}",
        )
        await message.answer(
            f"⏳ Первый полез. Остальным {DND_ACTION_WINDOW_SECONDS} секунд на свои действия."
        )
