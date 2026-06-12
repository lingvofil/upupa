# === wrapper.py ===

import os
import time
import logging
import threading
from typing import Optional, List, Any, Callable, Tuple
from groq import Groq
from PIL import Image
import base64
import io
from google import genai
from google.genai import types as genai_types
from gigachat import GigaChat
import requests

# =========================
# === RATE LIMIT CONTROL ===
# =========================
# Минимальный интервал между запросами НА ОДИН КЛЮЧ.
# Лимиты Google считаются по ключу, поэтому глобальный замок на все вызовы
# (как было раньше) душил параллельные чаты без всякой пользы.
PER_KEY_MIN_DELAY = 2.5

_last_call_ts: dict = {}
_throttle_lock = threading.Lock()
_genai_lock = threading.RLock()


def _throttle_key(api_key: str):
    """Выдерживает PER_KEY_MIN_DELAY между запросами на конкретный ключ.

    Разные ключи друг друга не блокируют. Потокобезопасно: слот времени
    резервируется под локом, ожидание — вне лока.
    """
    while True:
        with _throttle_lock:
            now = time.time()
            last = _last_call_ts.get(api_key, 0.0)
            wait = PER_KEY_MIN_DELAY - (now - last)
            if wait <= 0:
                _last_call_ts[api_key] = now
                return
        time.sleep(wait)


def _extract_error_details(error: Exception) -> Tuple[Optional[int], str]:
    status_code = getattr(error, "code", None) or getattr(error, "status_code", None)
    if status_code is None and hasattr(error, "response") and getattr(error, "response", None) is not None:
        status_code = getattr(error.response, "status_code", None)
    return status_code, error.__class__.__name__


def _is_retryable(error: Exception) -> bool:
    status_code, error_type = _extract_error_details(error)
    text = str(error).lower()
    if status_code in (429, 503):
        return True
    if error_type in ("ResourceExhausted", "QuotaExceeded"):
        return True
    return any(marker in text for marker in ("429", "503", "resourceexhausted", "quotaexceeded"))


# =========================
# === GOOGLE-GENAI ADAPTERS ===
# Новый SDK (google-genai) клиентный, старый (google.generativeai) — глобальный.
# Эти адаптеры дают СТАРЫЙ интерфейс (.generate_content / .start_chat / .history),
# чтобы не менять модули-потребители. Поддерживают легаси-формат контента
# {"mime_type": ..., "data": ...} и kwargs generation_config / safety_settings.
# =========================
_client_cache: dict = {}


def _get_client(api_key: str) -> "genai.Client":
    with _genai_lock:
        client = _client_cache.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            _client_cache[api_key] = client
        return client


def _normalize_part(item):
    """Старый блоб {"mime_type","data"} -> types.Part; остальное как есть."""
    if isinstance(item, dict) and set(item) == {"mime_type", "data"}:
        return genai_types.Part.from_bytes(data=item["data"], mime_type=item["mime_type"])
    return item


def _normalize_contents(contents):
    if isinstance(contents, (list, tuple)):
        return [_normalize_part(i) for i in contents]
    return contents


def _normalize_history(history):
    """Старая история [{'role','parts':[str,...]}] -> формат нового SDK."""
    if not history:
        return None
    out = []
    for item in history:
        if isinstance(item, dict):
            parts = [
                {"text": p} if isinstance(p, str) else p
                for p in item.get("parts", [])
            ]
            out.append({"role": item.get("role", "user"), "parts": parts})
        else:
            out.append(item)  # уже types.Content (например, из get_history)
    return out


def _build_config(kwargs: dict):
    """Собирает GenerateContentConfig из легаси-kwargs старого SDK.

    Поддерживает: generation_config (dict), safety_settings (dict или list),
    остальные ключи уходят в конфиг как есть (temperature и т.п.).
    """
    cfg = {}
    gen_cfg = kwargs.pop("generation_config", None)
    if gen_cfg:
        cfg.update(dict(gen_cfg))
    safety = kwargs.pop("safety_settings", None)
    if safety:
        if isinstance(safety, dict):  # старый формат {категория: порог}
            safety = [{"category": k, "threshold": v} for k, v in safety.items()]
        cfg["safety_settings"] = safety
    cfg.update(kwargs)
    return genai_types.GenerateContentConfig(**cfg) if cfg else None


class _ChatAdapter:
    def __init__(self, chat):
        self._chat = chat

    def send_message(self, content, **kwargs):
        config = _build_config(kwargs)
        content = _normalize_contents(content)
        if config is not None:
            return self._chat.send_message(content, config=config)
        return self._chat.send_message(content)

    @property
    def history(self):
        return self._chat.get_history()


class GeminiModel:
    """Старый интерфейс GenerativeModel поверх клиента google-genai."""

    def __init__(self, client: "genai.Client", model_name: str):
        self._client = client
        self.model_name = model_name

    def generate_content(self, contents, **kwargs):
        return self._client.models.generate_content(
            model=self.model_name,
            contents=_normalize_contents(contents),
            config=_build_config(kwargs),
        )

    def start_chat(self, history=None):
        return _ChatAdapter(
            self._client.chats.create(
                model=self.model_name,
                history=_normalize_history(history),
            )
        )


# =========================
# === FALLBACK CHAT SESSION ===
# =========================
class FallbackChatSession:
    def __init__(
        self,
        wrapper,
        history: Optional[List[Any]] = None,
        model_queue: Optional[List[str]] = None,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None
    ):
        self.wrapper = wrapper
        self.history = history or []
        self.model_queue = model_queue or wrapper.default_queue
        self.chat_id = chat_id
        self.user_id = user_id

    def send_message(self, content, chat_id=None, **kwargs):
        chat_chat_id = chat_id if chat_id is not None else self.chat_id
        response = self.wrapper._run_with_fallback(
            action_name="start_chat.send_message",
            chat_id=chat_chat_id,
            request_fn=lambda model_obj: self._send_with_model(model_obj, content, **kwargs)
        )
        return response

    def _send_with_model(self, model_obj, content, **kwargs):
        chat = model_obj.start_chat(history=self.history)
        response = chat.send_message(content, **kwargs)
        self.history = chat.history
        return response


# =========================
# === MODEL FALLBACK WRAPPER ===
# =========================
class ModelFallbackWrapper:
    GEMINI_LIMIT_EXHAUSTED_MESSAGE = "⚠️ Все лимиты Gemini временно исчерпаны. Попробуй позже."

    def __init__(self, default_queue: List[str], special_queue: List[str], keys_pool: Optional[List[str]] = None):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.keys_pool = [key for key in (keys_pool or []) if key]
        self._key_rr_cursor = 0
        self._max_retries_per_pair = 3
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        from core.settings import SPECIAL_CHAT_ID
        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(self, prompt, *, chat_id=None, **kwargs):
        return self._run_with_fallback(
            action_name="generate_content",
            chat_id=chat_id,
            request_fn=lambda model_obj: model_obj.generate_content(prompt, **kwargs)
        )

    def generate_custom(self, model_name: str, *args, **kwargs):
        model_name = self._normalize_model_name(model_name)
        temp_wrapper = ModelFallbackWrapper([model_name], [model_name], keys_pool=self.keys_pool)
        return temp_wrapper._run_with_fallback(
            action_name="generate_custom",
            chat_id=kwargs.pop("chat_id", None),
            request_fn=lambda model_obj: model_obj.generate_content(*args, **kwargs)
        )

    def start_chat(self, history=None, chat_id=None, user_id=None):
        queue = self._get_queue(chat_id)
        return FallbackChatSession(
            self,
            history=history,
            model_queue=queue,
            chat_id=chat_id,
            user_id=user_id
        )

    @property
    def model_names(self):
        return self.default_queue

    def _normalize_model_name(self, model_name: str) -> str:
        # google-genai принимает имя и с префиксом models/, и без; храним без
        return model_name.removeprefix("models/")

    def _iter_key_indices(self):
        if not self.keys_pool:
            return []
        count = len(self.keys_pool)
        start = self._key_rr_cursor % count
        order = [(start + i) % count for i in range(count)]
        self._key_rr_cursor = (start + 1) % count
        return order

    def _build_model(self, api_key: str, model_name: str):
        return GeminiModel(_get_client(api_key), model_name)

    def _run_with_fallback(self, action_name: str, chat_id: Optional[int], request_fn: Callable):
        model_queue = [self._normalize_model_name(name) for name in self._get_queue(chat_id)]
        key_indices = self._iter_key_indices()
        if not key_indices:
            raise RuntimeError("Gemini API keys pool is empty")

        hard_failures: List[Exception] = []
        temporary_failure_only = True
        attempts = 0

        for model_name in model_queue:
            for key_idx in key_indices:
                api_key = self.keys_pool[key_idx]
                for attempt in range(1, self._max_retries_per_pair + 1):
                    attempts += 1
                    try:
                        _throttle_key(api_key)
                        model_obj = self._build_model(api_key, model_name)
                        result = request_fn(model_obj)
                        self.last_used_model_name = model_name
                        logging.info(
                            "Gemini success action=%s key_idx=%s model=%s attempts=%s",
                            action_name, key_idx, model_name, attempt
                        )
                        return result
                    except Exception as error:
                        status_code, error_type = _extract_error_details(error)
                        retryable = _is_retryable(error)
                        logging.warning(
                            "Gemini fail action=%s key_idx=%s model=%s attempt=%s code=%s type=%s retryable=%s",
                            action_name, key_idx, model_name, attempt, status_code, error_type, retryable
                        )
                        if retryable and attempt < self._max_retries_per_pair:
                            time.sleep(2 ** (attempt - 1))
                            continue
                        if not retryable:
                            temporary_failure_only = False
                            hard_failures.append(error)
                        break

        if temporary_failure_only:
            raise RuntimeError(self.GEMINI_LIMIT_EXHAUSTED_MESSAGE)
        if hard_failures:
            raise RuntimeError(f"All Gemini models failed. Last error: {hard_failures[-1]}")
        raise RuntimeError("All Gemini models failed")


# =========================
# === GIGACHAT WRAPPER ===
# =========================
class GigaChatWrapper:
    def __init__(self, api_key: str, default_queue: List[str], special_queue: List[str]):
        self.api_key = api_key
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        from core.settings import SPECIAL_CHAT_ID
        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(self, prompt: str, *, chat_id=None, temperature: float = 0.7, **kwargs):
        """Генерация ответа с помощью GigaChat"""
        queue = self._get_queue(chat_id)
        
        for model_name in queue:
            try:
                with GigaChat(
                    credentials=self.api_key,
                    verify_ssl_certs=False,
                    temperature=temperature,
                    max_tokens=kwargs.get("max_tokens", 500),
                    model=model_name
                ) as giga:
                    response = giga.chat(prompt)
                    self.last_used_model_name = model_name
                    
                    # Создаём объект-обёртку для совместимости с Gemini API
                    class GigaResponse:
                        def __init__(self, text):
                            self.text = text
                    
                    return GigaResponse(response.choices[0].message.content)
                    
            except Exception as e:
                logging.error(f"GigaChat error [{model_name}]: {e}")
                continue
        
        raise RuntimeError("All GigaChat models failed")


# =========================
# === GROQ WRAPPER ===
# =========================
class GroqWrapper:
    def __init__(
        self, 
        api_key: str,
        vision_model: str = "meta-llama/llama-4-maverick-17b-128e-instruct",
        text_model: str = "openai/gpt-oss-120b",
        audio_model: str = "whisper-large-v3",
        tts_model: str = "canopylabs/orpheus-v1-english",
        summarization_model: str = "groq/compound-mini"
    ):
        self.client = Groq(api_key=api_key) if api_key else None
        
        # для чотам (картинки: считывание), скаламбурь, добавь, нарисуй, опиши
        self.vision_model = vision_model
        
        # для диалогов, пирожки, порошки, днд, чотам (текст, картинки: обработка считывания), 
        # пародия, кто я, что за чат, кем стать, викторина
        self.text_model = text_model

        # для чотам (аудио)
        self.audio_model = audio_model

        # для упупа скажи
        self.tts_model = tts_model

        # для чобыло
        self.summarization_model = summarization_model  
    
    def _prepare_image(self, image_bytes: bytes) -> str:
        """Оптимизация изображения для Groq (сжатие и конвертация в base64)"""
        img = Image.open(io.BytesIO(image_bytes))
        
        # Конвертируем в RGB если нужно (для PNG/WebP с прозрачностью)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        # Если картинка слишком большая, уменьшаем её (Groq рекомендует до 1-2МБ)
        max_size = 1280
        if max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    
    def analyze_image(self, image_bytes: bytes, prompt: str) -> str:
        """Анализ изображений (Vision) через Groq"""
        if not self.client: return "Ключ Groq не настроен"
        
        try:
            base64_image = self._prepare_image(image_bytes)
            
            completion = self.client.chat.completions.create(
                model=self.vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                        }
                    ]
                }],
                temperature=0.7,
                max_tokens=1024
            )
            return completion.choices[0].message.content or ""
        except Exception as e:
            logging.error(f"Groq Vision Error: {e}")
            raise
    
    def generate_text(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.7, presence_penalty: float = 0.0) -> str:
        """Генерация текста (LLM) через Groq"""
        if not self.client: return "Ключ Groq не настроен"
        try:
            completion = self.client.chat.completions.create(
                model=self.text_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                presence_penalty=presence_penalty,
                max_tokens=max_tokens
            )
            result = completion.choices[0].message.content
            logging.info(f"Groq generate_text: модель={self.text_model}, результат_длина={len(result) if result else 0}")
            return result or ""
        except Exception as e:
            logging.error(f"Groq Text Error: {e}", exc_info=True)
            raise
    
    def transcribe_audio(self, audio_bytes: bytes, file_name: str) -> str:
        """Транскрибация аудио (Whisper) через Groq"""
        if not self.client: return "Ключ Groq не настроен"
        try:
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = file_name 
            
            transcription = self.client.audio.transcriptions.create(
                file=audio_file,
                model=self.audio_model,
                response_format="text"
            )
            return transcription
        except Exception as e:
            logging.error(f"Groq Whisper Error: {e}")
            raise
# =========================
# === OPENAI-COMPATIBLE WRAPPER (OpenRouter, SiliconFlow) ===
# =========================
class OpenAICompatibleWrapper:
    """Универсальная обёртка для провайдеров с OpenAI-совместимым API."""

    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model_name = model_name

    def generate_text(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.7, presence_penalty: float = 0.0) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Обязательно для бесплатных моделей OpenRouter
            "HTTP-Referer": "https://github.com/upupa-bot",
            "X-Title": "UpupaBot",
        }
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "presence_penalty": presence_penalty,
            "max_tokens": max_tokens,
        }
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"]
            logging.info(
                f"OpenAICompatibleWrapper [{self.model_name}]: "
                f"получено {len(result) if result else 0} символов"
            )
            return result or ""
        except Exception as e:
            logging.error(f"OpenAICompatibleWrapper error [{self.model_name}]: {e}")
            raise
