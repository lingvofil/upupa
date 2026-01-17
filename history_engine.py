import re
import logging
from thefuzz import process
from config import LOG_FILE

def load_and_find_answer(user_input: str, chat_id: str):
    """
    Парсит лог-файл, ищет наиболее похожее сообщение и возвращает следующее за ним.
    """
    history = []
    # Регулярное выражение под ваш формат лога
    # Пример: 2025-12-22T07:26:05... - Chat -100151... - User 1285... [Юлия]: Текст
    pattern = rf" - Chat {chat_id} \(.*?\) - User \d+ \(.*?\) \[(.*?)\]: (.*)"
    
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line in lines:
            match = re.search(pattern, line)
            if match:
                name = match.group(1)
                text = match.group(2).strip()
                if text:  # Пропускаем пустые сообщения (медиа/стикеры)
                    history.append({"name": name, "text": text})
        
        if len(history) < 2:
            return None

        # Собираем все тексты (кроме самого последнего, так как на него нет ответа)
        all_texts = [msg['text'] for msg in history[:-1]]
        
        # Ищем совпадение (порог сходства 65%)
        best_match = process.extractOne(user_input, all_texts)
        
        if best_match and best_match[1] > 65:
            index = all_texts.index(best_match[0])
            # Берем следующее сообщение в истории как "ответ"
            response_msg = history[index + 1]
            return response_msg['text']
            
    except FileNotFoundError:
        logging.error(f"Log file {LOG_FILE} not found.")
    except Exception as e:
        logging.error(f"History engine error: {e}")
        
    return None
