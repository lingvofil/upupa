import re
import logging
import random
from thefuzz import process
from config import LOG_FILE

def load_and_find_answer(user_input: str, chat_id: str, depth: int = 2):
    """
    Парсит лог-файл и ищет контекстный ответ.
    :param depth: сколько последующих сообщений одного автора объединять в ответ.
    """
    history = []
    # Паттерн для разбора строк лога
    pattern = rf" - Chat {chat_id} \(.*?\) - User (\d+) \(.*?\) \[(.*?)\]: (.*)"
    
    try:
        if not os.path.exists(LOG_FILE):
            return None

        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line in lines:
            match = re.search(pattern, line)
            if match:
                user_id = match.group(1)
                name = match.group(2)
                text = match.group(3).strip()
                if text:
                    history.append({"user_id": user_id, "name": name, "text": text})
        
        if len(history) < 2:
            return None

        # Собираем список всех текстов для поиска (кроме последних сообщений)
        all_texts = [msg['text'] for msg in history[:-depth]]
        
        # Находим топ-3 похожих сообщений вместо одного (для вариативности)
        matches = process.extractBests(user_input, all_texts, score_cutoff=60, limit=3)
        
        if not matches:
            return None

        # Выбираем случайное из лучших совпадений
        chosen_match_text = random.choice(matches)[0]
        
        # Находим индексы всех вхождений этой фразы
        indices = [i for i, x in enumerate(all_texts) if x == chosen_match_text]
        start_index = random.choice(indices)
        
        # Собираем ответ
        response_parts = []
        target_user_id = history[start_index + 1]['user_id']
        
        # Берем сообщения, идущие подряд от одного и того же пользователя (имитируем поток мыслей)
        for i in range(1, depth + 1):
            next_msg = history[start_index + i]
            if next_msg['user_id'] == target_user_id:
                response_parts.append(next_msg['text'])
            else:
                break # Если начал отвечать другой юзер, останавливаемся

        return " ".join(response_parts)
            
    except Exception as e:
        logging.error(f"History engine error: {e}")
        
    return None
