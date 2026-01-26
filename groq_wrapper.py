# === groq_wrapper.py ===

import os
import logging
from typing import Optional
from groq import Groq
from PIL import Image
import base64
import io


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
    
    def generate_text(self, prompt: str, max_tokens: int = 1024) -> str:
        """Генерация текста (LLM) через Groq"""
        if not self.client: return "Ключ Groq не настроен"
        try:
            completion = self.client.chat.completions.create(
                model=self.text_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
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