"""Groq provider adapter."""

import base64
import io
import logging

from groq import Groq
from PIL import Image


class GroqWrapper:
    def __init__(
        self,
        api_key: str,
        vision_model: str = "meta-llama/llama-4-maverick-17b-128e-instruct",
        text_model: str = "openai/gpt-oss-120b",
        audio_model: str = "whisper-large-v3",
        tts_model: str = "canopylabs/orpheus-v1-english",
        summarization_model: str = "groq/compound-mini",
    ):
        self.client = Groq(api_key=api_key) if api_key else None
        self.vision_model = vision_model
        self.text_model = text_model
        self.audio_model = audio_model
        self.tts_model = tts_model
        self.summarization_model = summarization_model

    def _prepare_image(self, image_bytes: bytes) -> str:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        max_size = 1280
        if max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def analyze_image(self, image_bytes: bytes, prompt: str) -> str:
        if not self.client:
            return "Ключ Groq не настроен"

        try:
            base64_image = self._prepare_image(image_bytes)
            completion = self.client.chat.completions.create(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                },
                            },
                        ],
                    }
                ],
                temperature=0.7,
                max_tokens=1024,
            )
            return completion.choices[0].message.content or ""
        except Exception as exc:
            logging.error("Groq Vision Error: %s", exc)
            raise

    def generate_text(
        self,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        presence_penalty: float = 0.0,
    ) -> str:
        if not self.client:
            return "Ключ Groq не настроен"
        try:
            completion = self.client.chat.completions.create(
                model=self.text_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                presence_penalty=presence_penalty,
                max_tokens=max_tokens,
            )
            result = completion.choices[0].message.content
            logging.info(
                "Groq generate_text: модель=%s, результат_длина=%s",
                self.text_model,
                len(result) if result else 0,
            )
            return result or ""
        except Exception as exc:
            logging.error("Groq Text Error: %s", exc, exc_info=True)
            raise

    def transcribe_audio(self, audio_bytes: bytes, file_name: str) -> str:
        if not self.client:
            return "Ключ Groq не настроен"
        try:
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = file_name
            return self.client.audio.transcriptions.create(
                file=audio_file,
                model=self.audio_model,
                response_format="text",
            )
        except Exception as exc:
            logging.error("Groq Whisper Error: %s", exc)
            raise
