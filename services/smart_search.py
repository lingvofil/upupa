import logging
import numpy as np
from core.ai_clients import gemini_client
from config import model  # Используем настройку из конфига

# Если в config.py нет переменной EMBEDDING_MODEL, используем дефолтную
EMBEDDING_MODEL_NAME = 'models/text-embedding-004'

def get_embedding(text: str):
    """
    Превращает текст в вектор чисел.
    """
    try:
        # Используем genai напрямую, так как обертка model может не поддерживать embed_content
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=text,
            config={"task_type": "RETRIEVAL_DOCUMENT", "title": "User Message"},
        )
        return result.embeddings[0].values
    except Exception as e:
        logging.error(f"Ошибка получения эмбеддинга: {e}")
        return None

def cosine_similarity(v1, v2):
    """
    Считает, насколько похожи два вектора (от -1 до 1).
    """
    if v1 is None or v2 is None:
        return 0.0
    # Превращаем в numpy массивы
    v1 = np.array(v1)
    v2 = np.array(v2)
    
    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
        
    return np.dot(v1, v2) / (norm1 * norm2)

async def find_relevant_context(query_text: str, candidate_messages: list, top_k: int = 3):
    """
    Ищет в списке сообщений (candidate_messages) те, что по смыслу близки к query_text.
    
    :param query_text: Текст входящего сообщения (на что отвечаем)
    :param candidate_messages: Список строк (история сообщений пародируемого)
    :param top_k: Сколько примеров вернуть
    """
    if not candidate_messages or not query_text:
        return []

    # 1. Получаем вектор запроса
    try:
        query_embedding = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL_NAME,
            contents=query_text,
            config={"task_type": "RETRIEVAL_QUERY"},
        ).embeddings[0].values
    except Exception as e:
        logging.error(f"Не удалось получить вектор запроса: {e}")
        return []

    # 2. Оптимизация: чтобы не тратить квоты, берем не всю историю, 
    # а последние 100 сообщений + 50 случайных из глубины, если история большая
    # Но для качества лучше прогнать хотя бы 100-200 кандидатов.
    candidates_to_check = candidate_messages[-200:] # Берем последние 200 для контекста
    
    # 3. Получаем эмбеддинги для кандидатов (Batching)
    # API Gemini позволяет отправлять батчи, но для простоты пройдемся циклом или батчем
    # Если сообщений много, это может занять время.
    
    scored_messages = []
    
    # Группируем запросы (Batching) для ускорения, если библиотека поддерживает, 
    # но пока сделаем простой перебор для надежности
    
    # Ограничиваем количество запросов к API
    limit = 30 
    search_pool = candidates_to_check[-limit:] # Берем только последние 30 для скорости реал-тайма
    
    try:
        # Получаем эмбеддинги пачкой (поддерживается в новых версиях SDK)
        batch_embeddings = [
            e.values
            for e in gemini_client.models.embed_content(
                model=EMBEDDING_MODEL_NAME,
                contents=search_pool,
                config={"task_type": "RETRIEVAL_DOCUMENT"},
            ).embeddings
        ]
        
        for i, msg in enumerate(search_pool):
            score = cosine_similarity(query_embedding, batch_embeddings[i])
            scored_messages.append((score, msg))
            
    except Exception as e:
        logging.warning(f"Batch embedding failed, fallback to single: {e}")
        # Если батч не прошел, пробуем по одному (медленнее)
        for msg in search_pool:
            emb = get_embedding(msg)
            score = cosine_similarity(query_embedding, emb)
            scored_messages.append((score, msg))

    # Сортируем по похожести (от большего к меньшему)
    scored_messages.sort(key=lambda x: x[0], reverse=True)
    
    # Возвращаем только тексты топ-K
    # Фильтруем совсем непохожие (порог 0.4 например), чтобы не тянуть мусор
    result = [msg for score, msg in scored_messages[:top_k] if score > 0.35]
    
    return result
