# dnd_game.py

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PollAnswer,
)

from AI.summarize import _get_chat_messages
from core.paths import DND_STATE_PATH, USER_MESSAGES_LOG_PATH
from core.state import chat_settings
from infrastructure.ai.clients import gigachat_model, groq_ai, model


dnd_router = Router()

dnd_sessions = {}
poll_map = {}

DND_MODEL_TIMEOUT_SECONDS = 90
DND_POLL_TIMEOUT_SECONDS = 180
DND_ACTION_WINDOW_SECONDS = 180
DND_RECENT_SCENE_LIMIT = 2
DND_MAX_ROLL_DC = 17
DND_CHAT_BACKSTORY_HOURS = 12
DND_CHAT_BACKSTORY_MESSAGES = 100
DND_CHAT_BACKSTORY_MAX_CHARS = 8000
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
DND_ROLL_SKILLS = (
    "Акробатика",
    "Атлетика",
    "Внимательность",
    "Выживание",
    "Дрессировка",
    "Запугивание",
    "Исполнение",
    "История",
    "Ловкость рук",
    "Магия",
    "Медицина",
    "Обман",
    "Природа",
    "Проницательность",
    "Расследование",
    "Религия",
    "Скрытность",
    "Убеждение",
)
_DND_ROLL_SKILLS_BY_KEY = {skill.casefold(): skill for skill in DND_ROLL_SKILLS}

_task_supervisor = None
_finalizing_polls = set()
_processing_backstories = set()
_processing_lobby_starts = set()


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
   взаимодействие с окружением и последствия действий игроков. Свободный ход партии [ACTION:INPUT]
   используй чуть чаще: ориентир — примерно один раз в 2–3 сюжетных эпизода, если по смыслу не нужен
   бросок или голосование. Не делай два пустых групповых хода подряд без развития сцены.
5. Используй броски только когда исход действительно неопределён и важен. Здесь бросается чистый
   d20 БЕЗ модификаторов, поэтому не завышай сложность. Калибруй DC так:
   6–8 — легко; 9–10 — обычно; 11–12 — заметная трудность; 13–14 — сложно;
   15–16 — очень сложно; 17 — исключительная, редкая ситуация. DC 18–20 не назначай:
   без модификаторов они слишком суровы для этой упрощённой системы.
6. TYPE:CHECK — основной тип броска. Используй его для активных действий игрока: искать,
   замечать, расследовать, красться, убеждать, обманывать, запугивать, карабкаться, прыгать,
   вскрывать, выслеживать, вспоминать знания и т.п. TYPE:SAVE используй ТОЛЬКО когда персонаж
   реактивно сопротивляется уже возникшей опасности или эффекту: яд, обвал, падение, взрыв,
   потеря равновесия и подобное. Не делай спасброском обычную попытку что-то сделать или узнать.
7. Для CHECK по возможности указывай SKILL — смысловую категорию проверки. Допустимые значения:
   Акробатика, Атлетика, Внимательность, Выживание, Дрессировка, Запугивание, Исполнение,
   История, Ловкость рук, Магия, Медицина, Обман, Природа, Проницательность, Расследование,
   Религия, Скрытность, Убеждение. SKILL нужен только для разнообразия и понятного названия
   броска; он НЕ даёт бонусов и не требует листа персонажа. Если подходящего навыка нет, SKILL
   можно не указывать.
8. Не используй характеристики, модификаторы, бонусы персонажей или листы персонажей.
   REASON описывает конкретное действие или опасность в текущей сцене.
9. Преимущество или помеху назначай только когда это прямо следует из подготовки, позиции,
   помощи, состояния или окружения; не раздавай их каждому броску.
10. Если в запросе есть «РЕЖИМ С УЧАСТНИКАМИ ЧАТА», игроками считаются ТОЛЬКО люди из списка
    «УЧАСТНИКИ». У каждого есть числовой ID. Если текущая сцена обращается к конкретному персонажу
    или нескольким персонажам, ОБЯЗАТЕЛЬНО добавляй TARGETS с их ID в технический тег. Если ход или
    голосование действительно общее для всей партии, TARGETS не указывай. Не назначай игровым
    персонажем человека, которого нет в списке участников.

ФОРМАТ ТЕХНИЧЕСКИХ ТЕГОВ (В конце сообщения):

Общее голосование:
[ACTION:POLL;OPTIONS:Вариант 1;Вариант 2;Вариант 3]

Голосование только конкретных персонажей:
[ACTION:POLL;TARGETS:12345,67890;OPTIONS:Вариант 1;Вариант 2]

Проверка внимательности:
[ACTION:ROLL;TYPE:CHECK;SKILL:Внимательность;REASON:заметить движение в темноте;DC:10;MODE:NORMAL]

Адресная проверка персонажа:
[ACTION:ROLL;TYPE:CHECK;SKILL:Скрытность;REASON:тихо пройти мимо охраны;DC:11;MODE:ADVANTAGE;TARGETS:12345]

Спасбросок — только реакция на уже возникшую опасность:
[ACTION:ROLL;TYPE:SAVE;REASON:успеть отскочить от обвала;DC:11;MODE:DISADVANTAGE;TARGETS:12345]

TYPE: CHECK или SAVE.
SKILL: один из перечисленных навыков и только для CHECK; для SAVE не указывай.
MODE: NORMAL, ADVANTAGE или DISADVANTAGE.
REASON: коротко опиши, что именно сейчас пытается сделать или пережить персонаж.
TARGETS: числовые ID игроков через запятую; только когда действие относится к конкретным участникам.
При ADVANTAGE бросаются два d20 и берётся больший, при DISADVANTAGE — меньший.
Если преимущество/помеха не нужны, ставь NORMAL. Результат сравнивается с DC как чистый d20.

Общий свободный ход партии:
[ACTION:INPUT]

Свободный ход конкретного персонажа или нескольких персонажей:
[ACTION:INPUT;TARGETS:12345,67890]

Если игрок попросил завершить игру, опиши гибель и закончи тегом:
[ACTION:END]
"""


def _normalize_roll_skill(value) -> str | None:
    if not value:
        return None
    return _DND_ROLL_SKILLS_BY_KEY.get(str(value).strip().casefold())


def configure_task_supervisor(supervisor):
    global _task_supervisor
    _task_supervisor = supervisor


def _start_background_task(coro, *, name: str):
    if _task_supervisor is None:
        coro.close()
        raise RuntimeError("DnD task supervisor is not configured")
    return _task_supervisor.start(coro, name=name)


def get_active_model(chat_id):
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
        starter_user_id=None,
        active_model=None,
        conversation=None,
    ):
        self.chat_id = chat_id
        self.active_model = active_model or get_active_model(chat_id)
        self.starter_user_id = int(starter_user_id) if starter_user_id is not None else None
        self.starter_name = starter_name or "игрок"
        self.mode = None
        self.state = "WAITING_MODE"
        self.mode_prompt_message_id = None
        self.lobby_message_id = None
        self.participants = {}
        self.backstory_prompt_message_id = None
        self.last_roll_stat = None
        self.pending_roll = None
        self.current_poll_id = None
        self.pending_poll = None
        self.action_prompt_message_id = None
        self.pending_actions = {}
        self.action_deadline = None
        self.action_target_user_ids = []
        self.recent_scene_types = []

        if conversation is None:
            self.conversation = [
                {
                    "role": "user",
                    "content": (
                        DND_SYSTEM_PROMPT
                        + "\n\n"
                        + f"Инициатор игры: {self.starter_name}. Помни: не более 100 слов."
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
            "starter_user_id": self.starter_user_id,
            "starter_name": self.starter_name,
            "mode": self.mode,
            "state": self.state,
            "mode_prompt_message_id": self.mode_prompt_message_id,
            "lobby_message_id": self.lobby_message_id,
            "participants": self.participants,
            "backstory_prompt_message_id": self.backstory_prompt_message_id,
            "last_roll_stat": self.last_roll_stat,
            "pending_roll": self.pending_roll,
            "current_poll_id": self.current_poll_id,
            "pending_poll": self.pending_poll,
            "action_prompt_message_id": self.action_prompt_message_id,
            "pending_actions": self.pending_actions,
            "action_deadline": self.action_deadline,
            "action_target_user_ids": self.action_target_user_ids,
            "recent_scene_types": self.recent_scene_types,
        }

    @classmethod
    def from_record(cls, record):
        session = cls(
            int(record["chat_id"]),
            starter_name=record.get("starter_name") or "игрок",
            starter_user_id=record.get("starter_user_id"),
            active_model=record.get("active_model") or "gemini",
            conversation=record.get("conversation") or None,
        )
        session.mode = record.get("mode")
        session.state = record.get("state") or "WAITING_ACTION"
        if session.mode is None and session.state not in {"WAITING_MODE", "LOBBY"}:
            session.mode = "abstract"
        session.mode_prompt_message_id = record.get("mode_prompt_message_id")
        session.lobby_message_id = record.get("lobby_message_id")
        session.participants = {
            str(key): dict(value)
            for key, value in (record.get("participants") or {}).items()
            if isinstance(value, dict)
        }
        session.backstory_prompt_message_id = record.get("backstory_prompt_message_id")
        session.last_roll_stat = record.get("last_roll_stat")
        raw_roll = record.get("pending_roll") or None
        if raw_roll:
            raw_type = raw_roll.get("type", "CHECK")
            session.pending_roll = {
                "type": raw_type,
                "skill": (
                    _normalize_roll_skill(raw_roll.get("skill"))
                    if raw_type == "CHECK"
                    else None
                ),
                "reason": raw_roll.get("reason") or "проверка по ситуации",
                "dc": raw_roll.get("dc"),
                "mode": raw_roll.get("mode", "NORMAL"),
                "target_user_ids": [int(value) for value in raw_roll.get("target_user_ids", [])],
            }
        elif session.state == "WAITING_ROLL":
            session.pending_roll = {
                "type": "CHECK",
                "skill": None,
                "reason": "проверка по ситуации",
                "dc": None,
                "mode": "NORMAL",
                "target_user_ids": [],
            }
        session.current_poll_id = record.get("current_poll_id")
        session.pending_poll = record.get("pending_poll")
        if session.pending_poll is not None:
            session.pending_poll = dict(session.pending_poll)
            session.pending_poll["votes"] = dict(session.pending_poll.get("votes") or {})
            session.pending_poll["target_user_ids"] = [
                int(value) for value in session.pending_poll.get("target_user_ids", [])
            ]
        session.action_prompt_message_id = record.get("action_prompt_message_id")
        session.pending_actions = dict(record.get("pending_actions") or {})
        session.action_deadline = record.get("action_deadline")
        session.action_target_user_ids = [
            int(value) for value in record.get("action_target_user_ids", [])
        ]
        session.recent_scene_types = list(record.get("recent_scene_types") or [])[ 
            -DND_RECENT_SCENE_LIMIT:
        ]
        return session


def _state_path() -> Path:
    return Path(DND_STATE_PATH)


def persist_dnd_sessions() -> None:
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
    recent = set(session.recent_scene_types[-DND_RECENT_SCENE_LIMIT:])
    candidates = [scene for scene in DND_SCENE_TYPES if scene not in recent]
    if not candidates:
        candidates = list(DND_SCENE_TYPES)
    scene_type = random.choice(candidates)
    session.recent_scene_types.append(scene_type)
    session.recent_scene_types = session.recent_scene_types[-DND_RECENT_SCENE_LIMIT:]
    return scene_type


def with_scene_direction(session: GameSession, prompt: str) -> str:
    scene_type = choose_next_scene_type(session)
    return (
        f"{prompt}\n\n"
        f"РЕЖИССЁР СЦЕНЫ: {scene_type}. "
        "Сделай этот тип доминирующим в ближайшем сюжетном эпизоде, "
        "не называй его игрокам и не ломай текущую причинность."
    )


def _mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎲 Абстрактная история",
                    callback_data="dnd:mode:abstract",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 С участниками чата",
                    callback_data="dnd:mode:participants",
                )
            ],
        ]
    )


def _lobby_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🙋 Участвовать", callback_data="dnd:lobby:join")],
            [InlineKeyboardButton(text="▶️ Начать игру", callback_data="dnd:lobby:start")],
        ]
    )


def _participant_ids(session) -> set[int]:
    return {
        int(item.get("user_id"))
        for item in (getattr(session, "participants", {}) or {}).values()
        if item.get("user_id") is not None
    }


def _participant_name(session, user_id: int) -> str:
    item = (getattr(session, "participants", {}) or {}).get(str(int(user_id)))
    if item:
        return item.get("name") or f"игрок {user_id}"
    return f"игрок {user_id}"


def _target_names(session, target_user_ids: list[int]) -> list[str]:
    return [_participant_name(session, user_id) for user_id in target_user_ids]


def _is_participant_mode(session) -> bool:
    return getattr(session, "mode", None) == "participants"


def _can_user_act(session, user_id: int, target_user_ids=None) -> bool:
    if not _is_participant_mode(session):
        return True
    user_id = int(user_id)
    participants = _participant_ids(session)
    if user_id not in participants:
        return False
    targets = {int(value) for value in (target_user_ids or [])}
    return not targets or user_id in targets


def _parse_targets(command_str: str) -> list[int]:
    match = re.search(r"(?:^|;)TARGETS:([0-9,\s]+)(?:;|$)", command_str, flags=re.IGNORECASE)
    if not match:
        return []
    result = []
    for token in match.group(1).split(","):
        token = token.strip()
        if token.isdigit():
            value = int(token)
            if value not in result:
                result.append(value)
    return result


def _resolve_targets(session, raw_targets: list[int]) -> list[int]:
    if not raw_targets or not _is_participant_mode(session):
        return []
    participants = _participant_ids(session)
    valid = [user_id for user_id in raw_targets if user_id in participants]
    if raw_targets and not valid:
        logging.warning(
            "DnD model emitted unknown targets chat_id=%s targets=%s participants=%s",
            session.chat_id,
            raw_targets,
            sorted(participants),
        )
    return valid


def _action_prompt_text(session) -> str:
    targets = list(getattr(session, "action_target_user_ids", []) or [])
    if _is_participant_mode(session):
        if targets:
            names = ", ".join(_target_names(session, targets))
            heading = f"🎭 Ход: {names}. Только они могут ответить реплаем на это сообщение."
        else:
            names = ", ".join(
                item.get("name") or "Игрок"
                for item in (getattr(session, "participants", {}) or {}).values()
            )
            heading = "🎭 Ход партии. Пишите действия реплаями на это сообщение."
            if names:
                heading += f" Участники: {names}."
    else:
        heading = "🎭 Ход партии. Пишите действия реплаями на это сообщение."
    return (
        heading
        + "\n"
        + f"После первого действия собираю остальные ещё {DND_ACTION_WINDOW_SECONDS} секунд. "
        + "Каждый игрок может переписать своё действие новым реплаем. "
        + "Ведущий может завершить ход раньше командой «дальше»."
    )


def _lobby_text(session) -> str:
    participants = list((getattr(session, "participants", {}) or {}).values())
    if participants:
        roster = "\n".join(f"• {item.get('name') or 'Игрок'}" for item in participants)
    else:
        roster = "Пока никто не записался."
    return (
        "👥 Игра с участниками чата.\n"
        f"Ведущий: {getattr(session, 'starter_name', None) or 'игрок'}\n\n"
        f"Участники:\n{roster}\n\n"
        "Нажмите «Участвовать», если хотите стать персонажем. "
        "Когда все отметятся, ведущий нажимает «Начать игру»."
    )


async def open_action_window(bot: Bot, chat_id: int, target_user_ids=None):
    session = dnd_sessions.get(chat_id)
    if not session:
        return None
    session.state = "WAITING_ACTION"
    session.pending_roll = None
    session.pending_actions = {}
    session.action_deadline = None
    session.action_prompt_message_id = None
    session.action_target_user_ids = _resolve_targets(session, list(target_user_ids or []))
    persist_dnd_sessions()
    prompt_message = await bot.send_message(chat_id, _action_prompt_text(session))
    session.action_prompt_message_id = prompt_message.message_id
    persist_dnd_sessions()
    return prompt_message


async def _restore_mode_prompt(bot: Bot, chat_id: int):
    session = dnd_sessions.get(chat_id)
    if not session or session.state != "WAITING_MODE" or session.mode_prompt_message_id:
        return
    prompt = await bot.send_message(
        chat_id,
        f"{session.starter_name}, какую историю запускаем?",
        reply_markup=_mode_keyboard(),
    )
    session.mode_prompt_message_id = prompt.message_id
    persist_dnd_sessions()


async def _restore_lobby_prompt(bot: Bot, chat_id: int):
    session = dnd_sessions.get(chat_id)
    if not session or session.state != "LOBBY" or session.lobby_message_id:
        return
    prompt = await bot.send_message(
        chat_id,
        _lobby_text(session),
        reply_markup=_lobby_keyboard(),
    )
    session.lobby_message_id = prompt.message_id
    persist_dnd_sessions()


async def _restore_backstory_prompt(bot: Bot, chat_id: int):
    session = dnd_sessions.get(chat_id)
    if not session or session.state != "WAITING_BACKSTORY":
        return
    if getattr(session, "backstory_prompt_message_id", None):
        return
    prompt_message = await bot.send_message(
        chat_id,
        "Какую предысторию хочешь? (Ответь реплаем)",
    )
    session.backstory_prompt_message_id = prompt_message.message_id
    persist_dnd_sessions()


async def _restore_action_prompt(bot: Bot, chat_id: int):
    session = dnd_sessions.get(chat_id)
    if not session or session.state != "WAITING_ACTION":
        return
    if getattr(session, "action_prompt_message_id", None):
        return
    await open_action_window(
        bot,
        chat_id,
        target_user_ids=getattr(session, "action_target_user_ids", []),
    )


def restore_dnd_sessions(bot: Bot) -> int:
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
            elif session.state == "WAITING_POLL":
                session.state = "WAITING_ACTION"
                session.current_poll_id = None
                session.pending_poll = None

            if session.state == "RESOLVING":
                session.state = "WAITING_ACTION"
                session.action_prompt_message_id = None
                session.pending_actions = {}
                session.action_deadline = None
                session.pending_roll = None

            if session.state == "WAITING_MODE" and not session.mode_prompt_message_id:
                _start_background_task(
                    _restore_mode_prompt(bot, session.chat_id),
                    name=f"dnd-mode:{session.chat_id}:restore-prompt",
                )
            elif session.state == "LOBBY" and not session.lobby_message_id:
                _start_background_task(
                    _restore_lobby_prompt(bot, session.chat_id),
                    name=f"dnd-lobby:{session.chat_id}:restore-prompt",
                )
            elif (
                session.state == "WAITING_BACKSTORY"
                and not getattr(session, "backstory_prompt_message_id", None)
            ):
                _start_background_task(
                    _restore_backstory_prompt(bot, session.chat_id),
                    name=f"dnd-backstory:{session.chat_id}:restore-prompt",
                )

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


async def create_game_session(chat_id: int, starter_name: str, starter_user_id=None):
    return await asyncio.wait_for(
        asyncio.to_thread(
            GameSession,
            chat_id,
            starter_name,
            starter_user_id=starter_user_id,
        ),
        timeout=DND_MODEL_TIMEOUT_SECONDS,
    )


async def generate_session_response(session: GameSession, prompt: str) -> str:
    result = await asyncio.wait_for(
        asyncio.to_thread(session.send_message, prompt),
        timeout=DND_MODEL_TIMEOUT_SECONDS,
    )
    if dnd_sessions.get(session.chat_id) is session:
        persist_dnd_sessions()
    return result


def _parse_roll_command(command_str: str) -> dict:
    fields = {}
    for part in command_str.split(";")[1:]:
        key, separator, value = part.partition(":")
        if separator and value.strip():
            fields[key.strip().upper()] = value.strip()

    roll_type = fields.get("TYPE", "CHECK").upper()
    if roll_type not in {"CHECK", "SAVE"}:
        roll_type = "CHECK"

    skill = _normalize_roll_skill(fields.get("SKILL")) if roll_type == "CHECK" else None

    mode = fields.get("MODE", "NORMAL").upper()
    mode_aliases = {
        "ADV": "ADVANTAGE",
        "DIS": "DISADVANTAGE",
        "ПРЕИМУЩЕСТВО": "ADVANTAGE",
        "ПОМЕХА": "DISADVANTAGE",
    }
    mode = mode_aliases.get(mode, mode)
    if mode not in {"NORMAL", "ADVANTAGE", "DISADVANTAGE"}:
        mode = "NORMAL"

    dc = None
    if "DC" in fields:
        try:
            dc = max(5, min(DND_MAX_ROLL_DC, int(fields["DC"])))
        except (TypeError, ValueError):
            dc = None

    return {
        "type": roll_type,
        "skill": skill,
        "reason": fields.get("REASON") or "проверка по ситуации",
        "dc": dc,
        "mode": mode,
        "target_user_ids": _parse_targets(command_str),
    }


def _roll_d20(mode: str) -> tuple[list[int], int]:
    first = random.randint(1, 20)
    if mode == "NORMAL":
        return [first], first
    second = random.randint(1, 20)
    rolls = [first, second]
    if mode == "ADVANTAGE":
        return rolls, max(rolls)
    if mode == "DISADVANTAGE":
        return rolls, min(rolls)
    return [first], first


def _roll_type_label(roll_type: str, skill: str | None = None) -> str:
    if roll_type == "SAVE":
        return "Спасбросок"
    return skill or "Бросок"


def _roll_mode_label(mode: str) -> str:
    return {
        "ADVANTAGE": "преимущество",
        "DISADVANTAGE": "помеха",
        "NORMAL": "обычный бросок",
    }.get(mode, "обычный бросок")


def _format_roll_dice(rolls: list[int], result: int) -> str:
    if len(rolls) == 1:
        return str(result)
    return f"{rolls[0]} и {rolls[1]} → {result}"


def _roll_outcome(result: int, dc: int | None) -> str | None:
    if dc is None:
        return None
    return "успех" if result >= dc else "провал"


def _natural_roll_note(result: int) -> str | None:
    if result == 20:
        return "натуральная 20"
    if result == 1:
        return "натуральная 1"
    return None


def _poll_question(session, targets: list[int]) -> str:
    if targets:
        names = ", ".join(_target_names(session, targets))
        return f"{names}: чё делать будем?"[:300]
    return "Чё делать будем?"


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
            targets = _resolve_targets(session, _parse_targets(command_str))
            session.state = "WAITING_POLL"
            session.pending_roll = None
            session.action_prompt_message_id = None
            session.pending_actions = {}
            session.action_deadline = None
            session.action_target_user_ids = []
            poll_msg = await bot.send_poll(
                chat_id=chat_id,
                question=_poll_question(session, targets),
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
                "target_user_ids": targets,
                "votes": {},
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
        except Exception:
            logging.exception("DnD poll setup failed chat_id=%s", chat_id)
            session.current_poll_id = None
            session.pending_poll = None
            await bot.send_message(chat_id, "(Опрос развалился. Решайте словами).")
            await open_action_window(bot, chat_id)

    elif command_str.startswith("ROLL"):
        roll = _parse_roll_command(command_str)
        roll["target_user_ids"] = _resolve_targets(session, roll.get("target_user_ids", []))
        session.pending_roll = roll
        session.last_roll_stat = None
        session.state = "WAITING_ROLL"
        session.action_prompt_message_id = None
        session.pending_actions = {}
        session.action_deadline = None
        session.action_target_user_ids = []
        persist_dnd_sessions()
        details = [
            f"🎲 {_roll_type_label(roll['type'], roll.get('skill'))}: {roll['reason']}"
        ]
        if roll["dc"] is not None:
            details.append(f"сложность {roll['dc']}")
        if roll["mode"] != "NORMAL":
            details.append(_roll_mode_label(roll["mode"]))
        if roll.get("target_user_ids"):
            details.append("кидает " + ", ".join(_target_names(session, roll["target_user_ids"])))
        await bot.send_message(chat_id, ", ".join(details) + ". Пиши «кидаю».")
    elif command_str.startswith("INPUT"):
        targets = _resolve_targets(session, _parse_targets(command_str))
        await open_action_window(bot, chat_id, target_user_ids=targets)
    elif command_str.startswith("END"):
        cleanup_session(chat_id)
        await bot.send_message(chat_id, "☠️ Игра окончена.")


def cleanup_session(chat_id):
    session = dnd_sessions.pop(chat_id, None)
    if session and session.current_poll_id:
        poll_map.pop(str(session.current_poll_id), None)
    persist_dnd_sessions()
    logging.info("DnD session cleaned chat_id=%s", chat_id)


def _eligible_poll_vote_counts(session, options: list[str]) -> list[int]:
    counts = [0 for _ in options]
    poll = getattr(session, "pending_poll", None) or {}
    votes = poll.get("votes") or {}
    for option_id in votes.values():
        try:
            index = int(option_id)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(counts):
            counts[index] += 1
    return counts


def _outcome_from_counts(options: list[str], counts: list[int]) -> str:
    max_votes = max(counts, default=0)
    if max_votes > 0:
        winners = [option for option, count in zip(options, counts) if count == max_votes]
        return f"Выбор сделан: {random.choice(winners)}"
    return f"Тишина... Случайность выбрала: {random.choice(options)}"


async def finalize_poll(bot: Bot, chat_id: int, message_id: int, options: list):
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
            if _is_participant_mode(session):
                outcome = _outcome_from_counts(options, _eligible_poll_vote_counts(session, options))
            else:
                counts = [option.voter_count for option in poll_res.options]
                outcome = _outcome_from_counts(options, counts)
        except Exception:
            logging.exception("DnD stop_poll failed chat_id=%s poll_id=%s", chat_id, poll_id)
            if _is_participant_mode(session):
                outcome = _outcome_from_counts(options, _eligible_poll_vote_counts(session, options))
            else:
                outcome = f"Опрос потерялся, судьба выбрала: {random.choice(options)}"

        poll_map.pop(poll_id, None)
        session.current_poll_id = None
        session.pending_poll = None
        session.state = "RESOLVING"
        persist_dnd_sessions()
        await bot.send_message(chat_id, f"✅ {outcome}")
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
            await bot.send_message(chat_id, "Мастер на секунду выпал из реальности. История сохранена.")
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
    del poll_chat_id
    delay = DND_POLL_TIMEOUT_SECONDS if delay_seconds is None else max(0.0, delay_seconds)
    await asyncio.sleep(delay)
    session = dnd_sessions.get(chat_id)
    if not session or str(session.current_poll_id) != str(poll_id):
        return
    await finalize_poll(bot, chat_id, message_id, options)


def _format_group_actions(actions: list[dict]) -> str:
    return "\n".join(
        f"- {item.get('name') or 'Игрок'}: {item.get('action') or ''}" for item in actions
    )


async def finalize_group_actions(bot: Bot, chat_id: int, prompt_message_id: int):
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
    session.action_target_user_ids = []
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
        await bot.send_message(chat_id, "Мастер завис на коллективном безумии. Повторите действия.")
        await open_action_window(bot, chat_id)


async def wait_for_action_timeout(
    bot: Bot,
    chat_id: int,
    prompt_message_id: int,
    *,
    delay_seconds: float | None = None,
):
    delay = DND_ACTION_WINDOW_SECONDS if delay_seconds is None else max(0.0, delay_seconds)
    await asyncio.sleep(delay)
    session = dnd_sessions.get(chat_id)
    if not session or session.state != "WAITING_ACTION":
        return
    if int(session.action_prompt_message_id or 0) != int(prompt_message_id):
        return
    await finalize_group_actions(bot, chat_id, prompt_message_id)


def _poll_user_is_eligible(session, user_id: int) -> bool:
    poll = getattr(session, "pending_poll", None) or {}
    return _can_user_act(session, user_id, poll.get("target_user_ids") or [])


@dnd_router.poll_answer(lambda event: event.poll_id in poll_map)
async def handle_poll_answer(poll_answer: PollAnswer, bot: Bot):
    del bot
    chat_id = poll_map.get(poll_answer.poll_id)
    if not chat_id:
        return
    session = dnd_sessions.get(chat_id)
    if not session or not session.pending_poll:
        return
    user_id = int(poll_answer.user.id)
    if not _poll_user_is_eligible(session, user_id):
        logging.info(
            "DnD ignored ineligible poll vote chat_id=%s poll_id=%s user_id=%s",
            chat_id,
            poll_answer.poll_id,
            user_id,
        )
        return
    votes = session.pending_poll.setdefault("votes", {})
    if poll_answer.option_ids:
        votes[str(user_id)] = int(poll_answer.option_ids[0])
    else:
        votes.pop(str(user_id), None)
    persist_dnd_sessions()
    logging.info(
        "DnD poll vote chat_id=%s poll_id=%s user_id=%s",
        chat_id,
        poll_answer.poll_id,
        user_id,
    )


def _build_recent_chat_context(messages: list[dict]) -> str:
    selected_reversed = []
    used = 0
    for item in reversed(messages):
        line = f"{item.get('display_name') or 'Участник'}: {item.get('text') or ''}".strip()
        if not line:
            continue
        if not selected_reversed and len(line) > DND_CHAT_BACKSTORY_MAX_CHARS:
            return line[-DND_CHAT_BACKSTORY_MAX_CHARS:]
        extra = len(line) + (1 if selected_reversed else 0)
        if used + extra > DND_CHAT_BACKSTORY_MAX_CHARS:
            break
        selected_reversed.append(line)
        used += extra
    return "\n".join(reversed(selected_reversed))


async def _collect_recent_chat_context(chat_id: int) -> str:
    threshold = datetime.now() - timedelta(hours=DND_CHAT_BACKSTORY_HOURS)
    messages, _users, _chat_name = await asyncio.to_thread(
        _get_chat_messages,
        str(USER_MESSAGES_LOG_PATH),
        str(chat_id),
        threshold,
        DND_CHAT_BACKSTORY_MESSAGES,
        DND_CHAT_BACKSTORY_MESSAGES,
    )
    return _build_recent_chat_context(messages)


def _participants_prompt(session) -> str:
    lines = []
    for item in (session.participants or {}).values():
        user_id = int(item["user_id"])
        name = item.get("name") or f"Игрок {user_id}"
        lines.append(f"- ID {user_id}: {name}")
    return "\n".join(lines)


async def _start_participant_story(callback: CallbackQuery, session: GameSession):
    chat_id = session.chat_id
    if chat_id in _processing_lobby_starts:
        await callback.answer("Уже запускаю.")
        return
    _processing_lobby_starts.add(chat_id)
    try:
        session.state = "RESOLVING"
        persist_dnd_sessions()
        try:
            await callback.message.edit_text(
                "🎬 Запускаю историю с участниками:\n"
                + "\n".join(
                    f"• {item.get('name') or 'Игрок'}"
                    for item in session.participants.values()
                )
            )
        except Exception:
            pass
        context = await _collect_recent_chat_context(chat_id)
        if not context:
            context = "Свежей переписки почти нет. Придумай завязку вокруг самих участников."
        prompt = (
            "РЕЖИМ С УЧАСТНИКАМИ ЧАТА.\n"
            "УЧАСТНИКИ:\n"
            f"{_participants_prompt(session)}\n\n"
            "ПОСЛЕДНЯЯ ПЕРЕПИСКА ЧАТА:\n"
            f"{context}\n\n"
            "Возьми последнюю переписку как основу предыстории: используй её темы, шутки, "
            "свежие события и отношения между людьми, но не пересказывай лог буквально. "
            "Сделай зарегистрированных участников игровыми персонажами под их именами. "
            "Начинай историю до 100 слов. Если сцена сразу требует решения конкретного персонажа, "
            "укажи его ID через TARGETS."
        )
        response_text = await generate_session_response(
            session,
            with_scene_direction(session, prompt),
        )
        await parse_and_execute_turn(callback.bot, chat_id, response_text)
    except Exception:
        logging.exception("DnD participant story start failed chat_id=%s", chat_id)
        if dnd_sessions.get(chat_id) is session:
            session.state = "LOBBY"
            persist_dnd_sessions()
        await callback.message.answer("Мастер завис на старте. Лобби сохранено, нажми «Начать игру» ещё раз.")
    finally:
        _processing_lobby_starts.discard(chat_id)


@dnd_router.message(lambda m: m.text and m.text.lower().startswith("упупа начни историю"))
async def cmd_start_dnd(message: Message):
    user_name = message.from_user.first_name
    user_id = int(message.from_user.id)
    cleanup_session(message.chat.id)
    try:
        session = await create_game_session(message.chat.id, user_name, user_id)
        dnd_sessions[message.chat.id] = session
        persist_dnd_sessions()
    except asyncio.TimeoutError:
        await message.answer("Мастер завис в астрале. Попробуй начать историю ещё раз.")
        return
    except Exception:
        logging.exception("DnD session creation failed chat_id=%s", message.chat.id)
        await message.answer("Не удалось разбудить мастера историй.")
        return
    prompt_message = await message.answer(
        f"{user_name}, какую историю запускаем?",
        reply_markup=_mode_keyboard(),
    )
    session.mode_prompt_message_id = prompt_message.message_id
    persist_dnd_sessions()


def _callback_session(callback: CallbackQuery):
    if not callback.message:
        return None
    return dnd_sessions.get(callback.message.chat.id)


def _callback_is_host(callback: CallbackQuery, session) -> bool:
    starter_user_id = getattr(session, "starter_user_id", None)
    return starter_user_id is not None and int(callback.from_user.id) == int(starter_user_id)


@dnd_router.callback_query(F.data == "dnd:mode:abstract")
async def choose_abstract_mode(callback: CallbackQuery):
    session = _callback_session(callback)
    if not session or session.state != "WAITING_MODE":
        await callback.answer("Эта кнопка уже протухла.")
        return
    if not _callback_is_host(callback, session):
        await callback.answer("Режим выбирает тот, кто запустил историю.", show_alert=True)
        return
    session.mode = "abstract"
    session.state = "WAITING_BACKSTORY"
    session.mode_prompt_message_id = None
    persist_dnd_sessions()
    await callback.answer()
    try:
        await callback.message.edit_text("🎲 Абстрактная история.")
    except Exception:
        pass
    prompt_message = await callback.message.answer(
        f"Ладно, {session.starter_name}. Какую предысторию хочешь? (Ответь реплаем)"
    )
    session.backstory_prompt_message_id = prompt_message.message_id
    persist_dnd_sessions()


@dnd_router.callback_query(F.data == "dnd:mode:participants")
async def choose_participant_mode(callback: CallbackQuery):
    session = _callback_session(callback)
    if not session or session.state != "WAITING_MODE":
        await callback.answer("Эта кнопка уже протухла.")
        return
    if not _callback_is_host(callback, session):
        await callback.answer("Режим выбирает тот, кто запустил историю.", show_alert=True)
        return
    session.mode = "participants"
    session.state = "LOBBY"
    session.mode_prompt_message_id = None
    session.lobby_message_id = callback.message.message_id
    session.participants = {}
    persist_dnd_sessions()
    await callback.answer()
    await callback.message.edit_text(
        _lobby_text(session),
        reply_markup=_lobby_keyboard(),
    )


@dnd_router.callback_query(F.data == "dnd:lobby:join")
async def join_participant_lobby(callback: CallbackQuery):
    session = _callback_session(callback)
    if not session or session.state != "LOBBY" or session.mode != "participants":
        await callback.answer("Лобби уже закрыто.")
        return
    user_id = int(callback.from_user.id)
    session.participants[str(user_id)] = {
        "user_id": user_id,
        "name": callback.from_user.first_name or callback.from_user.full_name or "Игрок",
    }
    persist_dnd_sessions()
    await callback.answer("Ты в игре.")
    try:
        await callback.message.edit_text(
            _lobby_text(session),
            reply_markup=_lobby_keyboard(),
        )
    except Exception:
        pass


@dnd_router.callback_query(F.data == "dnd:lobby:start")
async def start_participant_lobby(callback: CallbackQuery):
    session = _callback_session(callback)
    if not session or session.state != "LOBBY" or session.mode != "participants":
        await callback.answer("Лобби уже закрыто.")
        return
    if not _callback_is_host(callback, session):
        await callback.answer("Запустить игру может только ведущий.", show_alert=True)
        return
    if not session.participants:
        await callback.answer("Сначала хотя бы кто-нибудь должен нажать «Участвовать».", show_alert=True)
        return
    await callback.answer("Поехали.")
    await _start_participant_story(callback, session)


@dnd_router.message(F.text.lower().startswith(("упупа заверши историю", "упупа закончи историю")))
async def cmd_stop_dnd(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session:
        await message.answer("Мы и не играем.")
        return
    if session.state in {"WAITING_MODE", "LOBBY", "WAITING_BACKSTORY"}:
        cleanup_session(message.chat.id)
        await message.answer("Игра отменена.")
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


def _is_backstory_reply(message: Message) -> bool:
    session = dnd_sessions.get(message.chat.id)
    if (
        not session
        or session.state != "WAITING_BACKSTORY"
        or message.chat.id in _processing_backstories
    ):
        return False
    starter_user_id = getattr(session, "starter_user_id", None)
    if starter_user_id is not None and int(message.from_user.id) != int(starter_user_id):
        return False
    prompt_message_id = getattr(session, "backstory_prompt_message_id", None)
    if not prompt_message_id or not message.reply_to_message:
        return False
    return int(message.reply_to_message.message_id) == int(prompt_message_id)


@dnd_router.message(_is_backstory_reply)
async def handle_backstory(message: Message):
    session = dnd_sessions[message.chat.id]
    backstory = message.text or message.caption
    if not backstory:
        await message.answer("Предысторию лучше прислать текстом.")
        return
    backstory_prompt_message_id = session.backstory_prompt_message_id
    _processing_backstories.add(message.chat.id)
    try:
        msg = await message.answer("Генерирую...")
        response_text = await generate_session_response(
            session,
            with_scene_direction(session, f"Предыстория: {backstory}. Начинай."),
        )
        try:
            await message.bot.delete_message(message.chat.id, msg.message_id)
        except Exception:
            pass
        session.backstory_prompt_message_id = None
        persist_dnd_sessions()
        await parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD backstory generation failed chat_id=%s", message.chat.id)
        if dnd_sessions.get(message.chat.id) is session:
            session.state = "WAITING_BACKSTORY"
            session.backstory_prompt_message_id = backstory_prompt_message_id
            persist_dnd_sessions()
        await message.answer("Мастер завис, но история сохранена. Попробуй ещё раз реплаем.")
    finally:
        _processing_backstories.discard(message.chat.id)


@dnd_router.message(F.text.lower().contains("кидаю"))
async def handle_roll(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session or session.state != "WAITING_ROLL":
        return

    roll = session.pending_roll or {
        "type": "CHECK",
        "skill": None,
        "reason": "проверка по ситуации",
        "dc": None,
        "mode": "NORMAL",
        "target_user_ids": [],
    }
    if not _can_user_act(session, int(message.from_user.id), roll.get("target_user_ids") or []):
        await message.answer("Этот бросок не твой.")
        return

    rolls, result = _roll_d20(roll.get("mode", "NORMAL"))
    roll_type = roll.get("type", "CHECK")
    skill = _normalize_roll_skill(roll.get("skill")) if roll_type == "CHECK" else None
    reason = roll.get("reason") or "проверка по ситуации"
    dc = roll.get("dc")
    mode = roll.get("mode", "NORMAL")
    outcome = _roll_outcome(result, dc)
    natural_note = _natural_roll_note(result)

    session.state = "RESOLVING"
    session.pending_roll = None
    persist_dnd_sessions()

    roll_label = _roll_type_label(roll_type, skill)
    result_lines = [f"🎲 {message.from_user.first_name}: {roll_label} — {reason}"]
    if dc is not None:
        difficulty_line = f"⚙️ Сложность — {dc}"
        if mode == "ADVANTAGE":
            difficulty_line += " (с преимуществом)"
        elif mode == "DISADVANTAGE":
            difficulty_line += " (с помехой)"
        result_lines.append(difficulty_line + ".")
    elif mode == "ADVANTAGE":
        result_lines.append("⚙️ Бросок с преимуществом.")
    elif mode == "DISADVANTAGE":
        result_lines.append("⚙️ Бросок с помехой.")

    if len(rolls) == 1:
        roll_line = f"🎯 Бросок кубика — {result}"
    else:
        roll_line = f"🎯 Броски кубика — {rolls[0]} и {rolls[1]}, результат — {result}"
    if natural_note:
        roll_line += f" ({natural_note})"
    result_lines.append(roll_line)
    await message.answer("\n".join(result_lines))

    if roll_type == "SAVE":
        prompt_roll_label = "спасбросок"
    elif skill:
        prompt_roll_label = f"проверку навыка «{skill}»"
    else:
        prompt_roll_label = "проверку"
    prompt_parts = [
        f"Игрок {message.from_user.first_name} сделал {prompt_roll_label}: {reason}.",
        f"Режим: {_roll_mode_label(mode)}.",
        f"Броски d20: {rolls}; итог: {result}.",
    ]
    if dc is not None:
        prompt_parts.append(f"Сложность: {dc}; результат: {outcome}.")
    else:
        prompt_parts.append("Сложность не была задана; трактуй число по ситуации.")
    if natural_note:
        prompt_parts.append(
            f"Выпала {natural_note}; отметь это в описании, но не меняй автоматически исход против сложности."
        )
    prompt_parts.append("Продолжай сюжет до 100 слов.")

    try:
        response_text = await generate_session_response(
            session,
            with_scene_direction(session, " ".join(prompt_parts)),
        )
        await parse_and_execute_turn(message.bot, message.chat.id, response_text)
    except Exception:
        logging.exception("DnD roll continuation failed chat_id=%s", message.chat.id)
        await message.answer("Мастер завис, но история сохранена.")
        await open_action_window(message.bot, message.chat.id)


def _is_group_action_reply(message: Message) -> bool:
    session = dnd_sessions.get(message.chat.id)
    if not session or session.state != "WAITING_ACTION":
        return False
    prompt_message_id = session.action_prompt_message_id
    if not prompt_message_id or not message.reply_to_message:
        return False
    user_action = message.text or message.caption
    if not user_action or user_action.lower().startswith("упупа"):
        return False
    if not _can_user_act(
        session,
        int(message.from_user.id),
        getattr(session, "action_target_user_ids", []) or [],
    ):
        return False
    return int(message.reply_to_message.message_id) == int(prompt_message_id)


@dnd_router.message(_is_group_action_reply)
async def handle_free_action(message: Message):
    session = dnd_sessions[message.chat.id]
    prompt_message_id = session.action_prompt_message_id
    user_action = message.text or message.caption
    if not user_action or user_action.lower().startswith("упупа"):
        return
    user_id = int(message.from_user.id)
    if not _can_user_act(
        session,
        user_id,
        getattr(session, "action_target_user_ids", []) or [],
    ):
        return
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
            wait_for_action_timeout(message.bot, message.chat.id, int(prompt_message_id)),
            name=f"dnd-actions:{message.chat.id}:{prompt_message_id}",
        )
        await message.answer(
            f"⏳ Первый полез. Остальным {DND_ACTION_WINDOW_SECONDS} секунд на свои действия. "
            "Ведущий может написать «дальше» и закончить ход раньше."
        )


def _is_dnd_next_command(message: Message) -> bool:
    if not message.text or message.text.strip().casefold() != "дальше":
        return False
    session = dnd_sessions.get(message.chat.id)
    return bool(session and session.state in {"WAITING_ACTION", "WAITING_POLL"})


@dnd_router.message(_is_dnd_next_command)
async def handle_dnd_next(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session:
        return
    starter_user_id = getattr(session, "starter_user_id", None)
    if starter_user_id is None or int(message.from_user.id) != int(starter_user_id):
        await message.answer("«Дальше» может сказать только ведущий.")
        return
    if session.state == "WAITING_ACTION":
        if not session.pending_actions:
            await message.answer("Пока нечего завершать: никто ещё не заявил действие.")
            return
        await finalize_group_actions(
            message.bot,
            message.chat.id,
            int(session.action_prompt_message_id),
        )
        return
    if session.state == "WAITING_POLL":
        poll = session.pending_poll or {}
        await finalize_poll(
            message.bot,
            message.chat.id,
            int(poll["message_id"]),
            list(poll["options"]),
        )
