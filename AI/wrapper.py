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
import google.generativeai as genai
from gigachat import GigaChat
import requests

# =========================
# === RATE LIMIT CONTROL ===
# =========================
GLOBAL_MIN_DELAY = 2.5

_last_call_ts = 0.0
_genai_lock = threading.RLock()


def _global_throttle():
    global _last_call_ts
    now = time.time()
    delta = now - _last_call_ts
    if delta < GLOBAL_MIN_DELAY:
        time.sleep(GLOBAL_MIN_DELAY - delta)
    _last_call_ts = time.time()


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
        from config import SPECIAL_CHAT_ID
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
        if not model_name.startswith("models/"):
            return f"models/{model_name}"
        return model_name

    def _iter_key_indices(self):
        if not self.keys_pool:
            return []
        count = len(self.keys_pool)
        start = self._key_rr_cursor % count
        order = [(start + i) % count for i in range(count)]
        self._key_rr_cursor = (start + 1) % count
        return order

    def _build_model(self, api_key: str, model_name: str):
        with _genai_lock:
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(model_name)

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
                        _global_throttle()
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
        from config import SPECIAL_CHAT_ID
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
