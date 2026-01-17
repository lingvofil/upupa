import re
import logging
import random
import os  # Добавлен обязательный импорт
from thefuzz import process
from config import LOG_FILE

def load_and_find_answer(user_input: str, chat_id: str, depth: int = 3):
    """
    Ищет ответ в локальных логах.
    :param user_input: Что написал пользователь сейчас.
    :param chat_id: ID чата.
    :param depth: Глубина ответа (количество фраз).
    """
    history = []
    chat_id_str = str(chat_id)
    
    try:
        if not os.path.exists(LOG_FILE):
            logging.error(f"Файл логов не найден: {LOG_FILE}")
            return None

        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                # Фильтруем строки по ID чата для скорости
                if chat_id_str in line:
                    # Регулярка под формат лога: ищем текст сообщения в конце строки
                    # Обычно формат: ... [Имя]: Сообщение
                    match = re.search(r"User (\d+) \(.*?\) \[(.*?)\]: (.*)", line)
                    if match:
                        history.append({
                            "user_id": match.group(1),
                            "name": match.group(2),
                            "text": match.group(3).strip()
                        })
        
        if len(history) < 5:
            print(f"DEBUG: Мало данных в истории чата {chat_id}")
            return None

        # Сообщения для поиска (все, кроме самых последних)
        all_texts = [msg['text'] for msg in history[:-5]]
        
        # Ищем совпадения (порог 40 — найдет почти любое созвучное слово)
        matches = process.extractBests(user_input, all_texts, score_cutoff=40, limit=5)
        
        if not matches:
            # Попытка поиска по самому длинному слову
            words = sorted(user_input.split(), key=len, reverse=True)
            if words:
                matches = process.extractBests(words[0], all_texts, score_cutoff=40, limit=3)

        if not matches:
            print(f"DEBUG: Не найдено совпадений для '{user_input}'")
            return None

        # Выбираем случайное из лучших совпадений
        best_match_text = random.choice(matches)[0]
        print(f"DEBUG: Найдено совпадение: {best_match_text}")
        
        # Находим индексы всех таких фраз
        indices = [i for i, x in enumerate(all_texts) if x == best_match_text]
        start_index = random.choice(indices)
        
        # Собираем ответ из сообщений, которые шли СЛЕДУЮЩИМИ
        response_parts = []
        current_idx = start_index + 1
        
        if current_idx < len(history):
            first_responder_id = history[current_idx]['user_id']
            for i in range(depth):
                idx = current_idx + i
                if idx < len(history):
                    msg = history[idx]
                    # Берем сообщение, если это тот же автор или это первая фраза ответа
                    if msg['user_id'] == first_responder_id or i == 0:
                        response_parts.append(msg['text'])
                    else:
                        break
        
        final_answer = " ".join(response_parts)
        print(f"DEBUG: Сформирован ответ: {final_answer}")
        return final_answer if final_answer else None
            
    except Exception as e:
        logging.error(f"Ошибка в history_engine: {e}")
        return None
