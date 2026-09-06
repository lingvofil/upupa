import re
import logging
import random
import os
from thefuzz import process
from core.state import LOG_FILE
from core.history_store import get_history_repository

# === ПУЛЬТ УПРАВЛЕНИЯ РЕЖИМОМ ===
MATCH_THRESHOLD = 70      # Схожесть (0-100). Чем выше, тем строже контекст.
RESPONSE_DEPTH = 2        # Сколько сообщений брать. 1 - коротко, 3+ - монолог.
STRICT_USER = True        # True: брать сообщения только одного автора. False: брать кусок диалога всех подряд.
MIN_WORD_LEN = 3          # Игнорировать слова короче этого при поиске (предлоги и т.д.)
IGNORE_SHORT_MSG = True   # Игнорировать ответы из логов короче 2 символов (типа "п", "д", ".")

def load_and_find_answer(user_input: str, chat_id: str, depth: int = RESPONSE_DEPTH):
    repository = get_history_repository(LOG_FILE)
    if repository is not None:
        if repository.count(chat_id, min_chars=2) < 10:
            return None
        query = " ".join(word for word in user_input.split() if len(word) >= MIN_WORD_LEN) or user_input
        tail = repository.select(chat_id, limit=5, min_chars=2)
        candidates = repository.search(chat_id, query, limit=100)
        if not candidates:
            candidates = repository.select(chat_id, limit=2000, min_chars=2)
        candidates = [row for row in candidates if tail and row["id"] < tail[0]["id"]]
        matches = process.extractBests(query, [row["text"] for row in candidates],
                                       score_cutoff=MATCH_THRESHOLD, limit=5)
        if not matches:
            return None
        chosen = next(row for row in candidates if row["text"] == matches[0][0])
        following = [row for row in repository.context(chat_id, chosen["id"], depth + 5)
                     if row["id"] > chosen["id"] and (not IGNORE_SHORT_MSG or len(row["text"].strip()) >= 2)][:depth]
        parts = []
        for row in following:
            if STRICT_USER and row["user_id"] != following[0]["user_id"]:
                break
            parts.append(row["text"] if STRICT_USER else f"[{row['full_name']}]: {row['text']}")
        return " ".join(parts) or None
    history = []
    chat_id_str = str(chat_id)
    
    try:
        if not os.path.exists(LOG_FILE):
            return None

        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if chat_id_str in line:
                    # Регулярка под ваш формат лога
                    match = re.search(r"User (\d+) \(.*?\) \[(.*?)\]: (.*)", line)
                    if match:
                        text = match.group(3).strip()
                        if IGNORE_SHORT_MSG and len(text) < 2:
                            continue
                        history.append({
                            "user_id": match.group(1),
                            "text": text
                        })
        
        if len(history) < 10:
            return None

        # Очистка входного запроса для лучшего поиска
        clean_input = " ".join([w for w in user_input.split() if len(w) >= MIN_WORD_LEN])
        if not clean_input: clean_input = user_input

        all_texts = [msg['text'] for msg in history[:-5]]
        
        # Поиск совпадений
        matches = process.extractBests(clean_input, all_texts, score_cutoff=MATCH_THRESHOLD, limit=5)
        
        if not matches:
            return None

        # Выбор лучшего совпадения (можно брать [0] для точности или random для хаоса)
        best_match_text = matches[0][0] 
        
        indices = [i for i, x in enumerate(all_texts) if x == best_match_text]
        start_index = random.choice(indices)
        
        response_parts = []
        current_idx = start_index + 1
        
        if current_idx < len(history):
            first_responder_id = history[current_idx]['user_id']
            for i in range(depth):
                idx = current_idx + i
                if idx < len(history):
                    msg = history[idx]
                    
                    # Логика управления контекстом:
                    if STRICT_USER:
                        # Только если продолжает писать тот же человек
                        if msg['user_id'] == first_responder_id:
                            response_parts.append(msg['text'])
                        else:
                            break
                    else:
                        # Берем всё подряд (хаотичный диалог)
                        response_parts.append(f"[{msg.get('name', '???')}]: {msg['text']}")

        return " ".join(response_parts) if response_parts else None
            
    except Exception as e:
        logging.error(f"Error: {e}")
        return None
