import logging

from aiogram import Bot, types

from core.json_repository import JsonFileRepository, JsonRepository
from core.paths import CHAT_LIST_PATH, CHAT_SETTINGS_PATH
from core.settings import ADMIN_ID, SPECIAL_CHAT_ID
from core.state import chat_list, chat_settings, sms_disabled_chats


def _settings_repository() -> JsonFileRepository:
    return JsonFileRepository(CHAT_SETTINGS_PATH)


def _chats_repository() -> JsonFileRepository:
    return JsonFileRepository(CHAT_LIST_PATH)


# Функция загрузки настроек чатов при старте
def load_chat_settings(repository: JsonRepository | None = None):
    """Загрузить настройки, сохраняя identity глобального ``chat_settings``."""
    repo = repository or _settings_repository()

    try:
        data = repo.load()
    except FileNotFoundError:
        chat_settings.clear()
        logging.info("Файл chat_settings.json не найден, используется пустой словарь настроек.")
        return
    except Exception as e:
        logging.error(f"Ошибка при загрузке настроек чатов: {e}")
        chat_settings.clear()
        return

    if isinstance(data, dict):
        chat_settings.clear()
        chat_settings.update(data)
        logging.info(f"Загружены настройки для {len(chat_settings)} чатов.")
    else:
        chat_settings.clear()
        logging.warning("Файл chat_settings.json повреждён, используется пустой словарь настроек.")


# Функция сохранения настроек чатов в файл
def save_chat_settings(repository: JsonRepository | None = None):
    repo = repository or _settings_repository()
    try:
        repo.save(chat_settings)
        logging.info("Настройки чатов сохранены.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении настроек чатов: {e}")


# Функция загрузки списка чатов при старте
def load_chats(repository: JsonRepository | None = None):
    repo = repository or _chats_repository()

    try:
        data = repo.load()
    except FileNotFoundError:
        chat_list.clear()
        return
    except Exception as e:
        logging.error(f"Ошибка при загрузке списка чатов: {e}")
        chat_list.clear()
        return

    if isinstance(data, list):
        chat_list.clear()
        chat_list.extend(data)
        logging.info(f"Загружено {len(chat_list)} чатов из файла.")
    else:
        chat_list.clear()
        logging.warning("Файл chats.json повреждён, создан новый список чатов.")


# Функция сохранения списка чатов в файл
def save_chats(repository: JsonRepository | None = None):
    repo = repository or _chats_repository()
    try:
        repo.save(chat_list)
        logging.info("Список чатов сохранён.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении списка чатов: {e}")


def load_chat_state() -> None:
    """Явно загрузить всё файловое состояние этого feature на startup."""
    load_chat_settings()
    load_chats()


# Функция добавления чата (без дублирования)
def add_chat(chat_id, chat_title, chat_username=None):
    # Проверяем, есть ли уже этот чат в списке
    if not any(chat["id"] == chat_id for chat in chat_list):
        chat_info = {
            "id": chat_id,
            "title": chat_title,
            "username": chat_username if chat_username else None,
        }
        chat_list.append(chat_info)
        save_chats()  # Сохраняем изменения
        logging.info(f"Добавлен новый чат: {chat_title} ({chat_id})")

    # Перемещаем "особый" чат наверх
    chat_list.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)


# Функция удаления чата из списка и настроек
def remove_chat(chat_id):
    removed = False
    before_count = len(chat_list)
    chat_list[:] = [chat for chat in chat_list if chat.get("id") != chat_id]
    if len(chat_list) != before_count:
        removed = True
        save_chats()

    chat_id_str = str(chat_id)
    settings_removed = False
    if chat_id in chat_settings:
        del chat_settings[chat_id]
        settings_removed = True
    if chat_id_str in chat_settings:
        del chat_settings[chat_id_str]
        settings_removed = True
    if settings_removed:
        save_chat_settings()

    if removed or settings_removed:
        logging.info(f"Удален чат {chat_id} из списков/настроек.")

    return removed or settings_removed


# Улучшенная функция получения списка чатов
def get_chats_list(chat_id, chat_title, chat_username):
    # Добавляем текущий чат, если его нет
    add_chat(chat_id, chat_title, chat_username)

    # Фильтруем чаты без названия (где title == None)
    filtered_chats = [chat for chat in chat_list if chat.get("title")]
    if not filtered_chats:
        return "Я пока никуда не добавлен."

    # Сортируем список перед созданием нумерованного текста
    filtered_chats.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)

    # Создаем новый список с правильной нумерацией
    numbered_chats = []
    for i, chat in enumerate(filtered_chats):
        sms_disabled_badge = " (хуесосы-бирюки)" if str(chat["id"]) in sms_disabled_chats else ""
        if chat.get("username"):
            numbered_chats.append(f"{i+1}. {chat['title']} (@{chat['username']}){sms_disabled_badge}")
        else:
            numbered_chats.append(f"{i+1}. {chat['title']}{sms_disabled_badge}")

    response = "Тут:\n" + "\n".join(numbered_chats)
    return response


async def process_update_all_chats(message: types.Message, bot: Bot):
    """Попытка обновить информацию о всех чатах через API бота и удаление недоступных чатов"""
    if message.from_user.id != ADMIN_ID:  # Проверка на админа
        await message.reply("Иди нахуй, у тебя нет прав на это.")
        return

    try:
        updated_chats = []
        successful_updates = 0
        removed_chats = []

        bot_me = await bot.get_me()
        bot_id = bot_me.id

        for chat in chat_list:
            try:
                chat_info = await bot.get_chat(chat["id"])

                # For public chats get_chat can succeed even if the bot is not a member.
                # Verify membership for groups/supergroups/channels.
                chat_type = getattr(chat_info, "type", None)
                if chat_type in ["group", "supergroup", "channel"]:
                    try:
                        member = await bot.get_chat_member(chat["id"], bot_id)
                        if member.status in ["left", "kicked"]:
                            removed_chats.append({
                                "id": chat["id"],
                                "title": chat.get("title", "Unknown chat"),
                                "reason": f"bot status: {member.status}",
                            })
                            logging.info(
                                f"Removed chat {chat.get('title', chat['id'])}: bot status {member.status}"
                            )
                            continue
                    except Exception as e:
                        error_str = str(e)
                        if (
                            "bot was kicked" in error_str
                            or "bot was blocked" in error_str
                            or "chat not found" in error_str
                            or "member not found" in error_str
                        ):
                            removed_chats.append({
                                "id": chat["id"],
                                "title": chat.get("title", "Unknown chat"),
                                "reason": error_str,
                            })
                            logging.info(f"Removed chat {chat.get('title', chat['id'])}: {error_str}")
                            continue
                        logging.warning(f"Failed to verify membership for chat {chat['id']}: {e}")

                updated_chat = {
                    "id": chat["id"],
                    "title": chat_info.title,
                    "username": chat_info.username,
                }
                updated_chats.append(updated_chat)
                successful_updates += 1
            except Exception as e:
                error_str = str(e)
                if (
                    "bot was kicked" in error_str
                    or "bot was blocked" in error_str
                    or "chat not found" in error_str
                ):
                    removed_chats.append({
                        "id": chat["id"],
                        "title": chat.get("title", "Unknown chat"),
                        "reason": error_str,
                    })
                    logging.info(
                        f"Removed chat {chat.get('title', chat['id'])}: bot is no longer available there"
                    )
                else:
                    logging.warning(f"Failed to refresh chat {chat['id']}: {e}")
                    updated_chats.append(chat)

        current_chat_id = message.chat.id
        if not any(chat["id"] == current_chat_id for chat in updated_chats):
            updated_chats.append({
                "id": current_chat_id,
                "title": message.chat.title,
                "username": message.chat.username,
            })

        # Удаляем дубликаты и чаты без названия
        unique_chats = []
        unique_ids = set()
        for chat in updated_chats:
            if chat["id"] not in unique_ids and chat.get("title"):
                unique_ids.add(chat["id"])
                unique_chats.append(chat)

        # Обновляем глобальный список на месте
        chat_list.clear()
        chat_list.extend(unique_chats)

        if removed_chats:
            removed_ids = {c["id"] for c in removed_chats}
            settings_removed = False
            for rid in removed_ids:
                rid_str = str(rid)
                if rid in chat_settings:
                    del chat_settings[rid]
                    settings_removed = True
                if rid_str in chat_settings:
                    del chat_settings[rid_str]
                    settings_removed = True
            if settings_removed:
                save_chat_settings()

        # Сортируем чаты (специальный чат всегда первый)
        chat_list.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)

        # Обновляем индексы для всех чатов
        for i, chat in enumerate(chat_list):
            chat["index"] = i + 1

        save_chats()  # Сохраняем обновленный список

        # Формируем отчет об удаленных чатах
        removed_info = ""
        if removed_chats:
            removed_info = "\n\nУдаленные чаты:\n" + "\n".join([
                f"- {chat['title']} (ID: {chat['id']})"
                for chat in removed_chats
            ])

        # Формируем сообщение с результатами
        result_message = (
            f"Список чатов обновлен.\n"
            f"Успешно обновлено: {successful_updates}\n"
            f"Удалено чатов: {len(removed_chats)}"
            f"{removed_info}"
        )

        await message.reply(result_message)

    except Exception as e:
        logging.error(f"Ошибка при полном обновлении списка чатов: {e}")
        await message.reply("Произошла ошибка при обновлении списка чатов.")
