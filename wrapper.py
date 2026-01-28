# === wrapper.py (moved from groq_wrapper.py) ===

import os
import logging
import time
from typing import Optional, List, Any
from groq import Groq
from PIL import Image
import base64
import io

import google.generativeai as genai
from google.api_core import exceptions
from gigachat import GigaChat

# =========================
# === RATE LIMIT CONTROL ===
# =========================
GLOBAL_MIN_DELAY = 2.5
GEMINI_ACCOUNT_COOLDOWN = 300

_last_call_ts = 0.0
_gemini_blocked_until = 0.0


def _global_throttle():
    global _last_call_ts
    now = time.time()
    delta = now - _last_call_ts
    if delta < GLOBAL_MIN_DELAY:
        time.sleep(GLOBAL_MIN_DELAY - delta)
    _last_call_ts = time.time()


def _gemini_available() -> bool:
    return time.time() >= _gemini_blocked_until


def _block_gemini():
    global _gemini_blocked_until
    _gemini_blocked_until = time.time() + GEMINI_ACCOUNT_COOLDOWN
    logging.warning("⛔ Gemini account cooldown activated")


class GroqWrapper:
    def __init__(self, api_key: str):
        self.client = Groq(api_key=api_key) if api_key else None
        # Актуальные модели на текущий момент
        
        # для чотам (картинки: считывание), скаламбурь, добавь, нарисуй, опиши
        self.vision_model = "meta-llama/llama-4-maverick-17b-128e-instruct"
        
        # для диалогов, пирожки, порошки, днд, чотам (текст, картинки: обработка считывания), 
        # пародия, кто я, что за чат, кем стать, викторина
        self.text_model = "openai/gpt-oss-120b" 

        # для чотам (аудио)
        self.audio_model = "whisper-large-v3" 

        # для упупа скажи
        self.tts_model = "canopylabs/orpheus-v1-english"

        # для чобыло
        self.summarization_model = "groq/compound-mini"  
    
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
                            "image_url": {"url": f"data:image/jpeg;base64,{{base64_image}}"}
                        }
                    ]
                }],
                temperature=0.7,
                max_tokens=1024
            )
            return completion.choices[0].message.content or ""
        except Exception as e:
            logging.error(f"Groq Vision Error: {{e}}")
            raise
    
    def generate_text(self, prompt: str, max_tokens: int = 1024) -> str:
        """Генерация текста через Groq"""
        if not self.client:
            return "Ключ Groq не настроен"
        try:
            completion = self.client.chat.completions.create(
                model=self.text_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=max_tokens
            )
            return completion.choices[0].message.content or ""
        except Exception as e:
            logging.error(f"Groq Text Error: {{e}}")
            raise


# =========================
# === FALLBACK CHAT SESSION ===
# (перенесено из config.py)
# =========================
class FallbackChatSession:
    def __init__(self,
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

    def send_message(self, content):
        if not _gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        for model_name in self.model_queue:
            try:
                _global_throttle()
                model = genai.GenerativeModel(model_name)
                chat = model.start_chat(history=self.history)
                response = chat.send_message(content)
                self.history = chat.history
                self.wrapper.last_used_model_name = model_name
                return response

            except exceptions.ResourceExhausted:
                _block_gemini()
                raise

            except Exception as e:
                logging.error(f"Chat error [{{model_name}}]: {{e}}")

        raise RuntimeError("All Gemini chat models failed")


# =========================
# === MODEL FALLBACK WRAPPER ===
# (перенесено из config.py)
# =========================
class ModelFallbackWrapper:
    def __init__(self, default_queue: List[str], special_queue: List[str], special_chat_id: Optional[int] = None):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.special_chat_id = special_chat_id
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        if self.special_chat_id is not None and chat_id and str(chat_id) == str(self.special_chat_id):
            return self.special_queue
        return self.default_queue

    def generate_content(self, prompt, *, chat_id=None, **kwargs):
        if not _gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        for model_name in self._get_queue(chat_id):
            try:
                _global_throttle()
                model = genai.GenerativeModel(model_name)
                result = model.generate_content(prompt, **kwargs)
                self.last_used_model_name = model_name
                return result

            except exceptions.ResourceExhausted:
                _block_gemini()
                raise

            except Exception as e:
                logging.error(f"Generate error [{{model_name}}]: {{e}}")

        raise RuntimeError("All Gemini models failed")

    def generate_custom(self, model_name: str, *args, **kwargs):
        if not _gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        try:
            _global_throttle()
            model = genai.GenerativeModel(model_name)
            return model.generate_content(*args, **kwargs)

        except exceptions.ResourceExhausted:
            _block_gemini()
            raise

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


# =========================
# === GIGACHAT WRAPPER ===
# (перенесено из config.py)
# =========================
class GigaChatWrapper:
    def __init__(self, api_key: str, default_queue: List[str], special_queue: List[str], special_chat_id: Optional[int] = None):
        self.api_key = api_key
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.special_chat_id = special_chat_id
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        if self.special_chat_id is not None and chat_id and str(chat_id) == str(self.special_chat_id):
            return self.special_queue
        return self.default_queue

    def generate_content(self, prompt: str, *, chat_id=None):
        """Генерация ответа с помощью GigaChat"""
        queue = self._get_queue(chat_id)
        
        for model_name in queue:
            try:
                with GigaChat(
                    credentials=self.api_key,
                    verify_ssl_certs=False,
                    temperature=0.7,
                    max_tokens=500,
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
                logging.error(f"GigaChat error [{{model_name}}]: {{e}}")
                continue
        
        raise RuntimeError("All GigaChat models failed")
