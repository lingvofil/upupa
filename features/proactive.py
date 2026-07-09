# === features/proactive.py — проактивный режим ===
#
# Фоновый цикл: если в групповом чате долго тишина — бот сам вбрасывает
# тему по мотивам старой переписки (лог) или кидает мем.
# Включается/выключается тумблером в "упупа настройки" (proactive_enabled,
# по умолчанию включено). Последняя активность берётся из БД статистики,
# метка последнего вброса хранится в chat_settings.

import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta

import pytz

from config import DB_FILE, chat_settings
from features.chat_settings import save_chat_settings

CHECK_INTERVAL = 30 * 60            # проверка раз в полчаса
SILENCE_MIN_HOURS = 18              # молчание меньше — рано лезть
SILENCE_MAX_DAYS = 7                # молчание дольше — чат мёртв, не тревожим труп
COOLDOWN_HOURS = 40                 # не чаще одного вброса раз в 40 часов
ACTIVE_CHAT_WINDOW_DAYS = 30        # чаты без сообщений за месяц не трогаем
NIGHT_HOURS = range(0, 9)           # по Москве ночью молчим
MEME_PROBABILITY = 0.3              # 30% вбросов — мем, остальное — AI-тема
HISTORY_WINDOW = 25                 # сколько старых сообщений скармливать модели

MOSCOW_TZ = pytz.timezone("Europe/Moscow")


def is_proactive_enabled(chat_id: str) -> bool:
    return chat_settings.get(str(chat_id), {}).get("proactive_enabled", True)


def _get_group_chat_activity() -> dict[int, datetime]:
    """chat_id -> время последнего сообщения (только группы с активностью за месяц)."""
    result: dict[int, datetime] = {}
    try:
        conn = sqlite3.connect(DB_FILE)
        rows = conn.execute(
            """SELECT chat_id, MAX(message_timestamp) FROM message_stats
               WHERE is_private = 0 GROUP BY chat_id"""
        ).fetchall()
        conn.close()
    except Exception as e:
        logging.error(f"[proactive] не смог прочитать БД статистики: {e}")
        return result

    cutoff = datetime.now() - timedelta(days=ACTIVE_CHAT_WINDOW_DAYS)
    for chat_id, ts in rows:
        if not ts:
            continue
        try:
            last = datetime.fromisoformat(str(ts))
        except ValueError:
            continue
        if last >= cutoff:
            result[int(chat_id)] = last
    return result


def _get_last_proactive(chat_id: str) -> datetime | None:
    raw = chat_settings.get(chat_id, {}).get("last_proactive")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _mark_proactive(chat_id: str):
    chat_settings.setdefault(chat_id, {})["last_proactive"] = datetime.now().isoformat()
    save_chat_settings()


async def _send_ai_topic(bot, chat_id: int, silence_hours: int) -> bool:
    """Вбрасывает тему на основе случайного куска старой переписки."""
    from AI.chat_recall import _read_chat_log
    from AI.summarize import _generate_with_active_model

    messages = await asyncio.to_thread(_read_chat_log, str(chat_id))
    if len(messages) < HISTORY_WINDOW:
        return False

    start = random.randint(0, len(messages) - HISTORY_WINDOW)
    window = messages[start:start + HISTORY_WINDOW]
    old_dialog = "\n".join(f"{m['name']}: {m['text']}" for m in window)

    prompt = (
        f"В чате уже {silence_hours} часов тишина, все молчат. Ты — участник чата и хочешь его расшевелить.\n"
        f"Ниже кусок старой переписки этого чата. Напиши ОДНО короткое сообщение (до 40 слов): "
        f"вбрось тему, подколи кого-то из участников по мотивам их старых сообщений или спроси, "
        f"чем всё закончилось. С иронией, сарказмом и нецензурной лексикой. "
        f"Не здоровайся, не объясняй, что ты бот, просто пиши сообщение.\n\n"
        f"Старая переписка:\n{old_dialog}\n\nСообщение:"
    )
    try:
        text = await _generate_with_active_model(prompt, str(chat_id))
        if not text or not text.strip():
            return False
        await bot.send_message(chat_id, text.strip())
        return True
    except Exception as e:
        logging.warning(f"[proactive] AI-вброс в {chat_id} не удался: {e}")
        return False


async def _send_meme(bot, chat_id: int) -> bool:
    from services import memegenerator
    try:
        photo = await memegenerator.create_meme_image(chat_id, None)
        if not photo:
            return False
        await bot.send_photo(chat_id, photo, caption="Чото тихо у вас. Держите мем.")
        return True
    except Exception as e:
        logging.warning(f"[proactive] мем в {chat_id} не удался: {e}")
        return False


async def _poke_chat(bot, chat_id: int, last_activity: datetime):
    silence_hours = int((datetime.now() - last_activity).total_seconds() // 3600)
    sent = False
    if random.random() < MEME_PROBABILITY:
        sent = await _send_meme(bot, chat_id)
    if not sent:
        sent = await _send_ai_topic(bot, chat_id, silence_hours)
    if sent:
        logging.info(f"[proactive] вброс в чат {chat_id} (тишина {silence_hours}ч)")


async def proactive_loop(bot):
    """Фоновая задача: запускается из main.py."""
    await asyncio.sleep(120)  # даём боту подняться
    while True:
        try:
            now_msk = datetime.now(MOSCOW_TZ)
            if now_msk.hour not in NIGHT_HOURS:
                activity = await asyncio.to_thread(_get_group_chat_activity)
                for chat_id, last_activity in activity.items():
                    cid = str(chat_id)
                    if not is_proactive_enabled(cid):
                        continue
                    silence = datetime.now() - last_activity
                    if not (timedelta(hours=SILENCE_MIN_HOURS) <= silence <= timedelta(days=SILENCE_MAX_DAYS)):
                        continue
                    last_poke = _get_last_proactive(cid)
                    if last_poke and datetime.now() - last_poke < timedelta(hours=COOLDOWN_HOURS):
                        continue
                    # метку ставим до отправки: если чат недоступен (кикнули),
                    # не долбимся в него каждые полчаса
                    _mark_proactive(cid)
                    await _poke_chat(bot, chat_id, last_activity)
                    await asyncio.sleep(5)  # пауза между чатами
        except Exception as e:
            logging.error(f"[proactive] ошибка цикла: {e}", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL)
