"""Reusable text-to-speech pipeline.

This module deliberately does *not* know about Upupa's distortion effect.  It
turns plain text into clean speech and can therefore be reused by features
that need normal audio (Radio Upupa) and by callers that apply their own
post-processing afterwards (``упупа скажи``).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import wave
from dataclasses import dataclass
from io import BytesIO

from pydub import AudioSegment

from core.settings import GROQ_TTS_MODEL, TTS_MODELS_QUEUE
from infrastructure.ai.clients import gemini_client, groq_ai
from infrastructure.ai.gemini import GeminiModel


AVAILABLE_GEMINI_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
]

GROQ_TTS_VOICES = ["autumn", "diana", "hannah", "austin", "daniel", "troy"]

# Gemini 2.5 Flash/Pro TTS currently accept 8192 input tokens.  We use a much
# smaller character cap so long Russian scripts are split well before either
# the input or generated-audio token limit becomes relevant.
GEMINI_SAFE_CHUNK_CHARS = 3000

# Groq documents a hard 200-character limit for Orpheus input.  Keep a little
# room for punctuation/normalization changes.
GROQ_SAFE_CHUNK_CHARS = 190


@dataclass(frozen=True)
class SpeechAudio:
    data: bytes
    format: str
    provider: str
    chunks: int


class SpeechSynthesisError(RuntimeError):
    pass


def split_text_for_tts(text: str, max_chars: int) -> list[str]:
    """Split text on sentence/word boundaries without exceeding ``max_chars``."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    if max_chars < 20:
        raise ValueError("max_chars is too small for TTS chunking")

    sentences = re.split(r"(?<=[.!?…])\s+", normalized)
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            chunks.append(current.strip())
            current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) <= max_chars:
            candidate = f"{current} {sentence}".strip()
            if len(candidate) <= max_chars:
                current = candidate
            else:
                flush_current()
                current = sentence
            continue

        flush_current()
        words = sentence.split()
        part = ""
        for word in words:
            if len(word) > max_chars:
                if part:
                    chunks.append(part)
                    part = ""
                for start in range(0, len(word), max_chars):
                    chunks.append(word[start:start + max_chars])
                continue

            candidate = f"{part} {word}".strip()
            if len(candidate) <= max_chars:
                part = candidate
            else:
                if part:
                    chunks.append(part)
                part = word
        if part:
            current = part

    flush_current()
    return chunks


def _contains_cyrillic(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def _pcm_to_wav_bytes(pcm: bytes, sample_rate: int = 24000) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def _merge_wav_chunks_to_mp3(wav_chunks: list[bytes]) -> bytes:
    if not wav_chunks:
        raise SpeechSynthesisError("TTS returned no audio chunks")

    combined = AudioSegment.empty()
    for chunk in wav_chunks:
        combined += AudioSegment.from_file(BytesIO(chunk), format="wav")

    output = BytesIO()
    combined.export(output, format="mp3", bitrate="128k")
    return output.getvalue()


def _gemini_tts_sync(text: str, voice_name: str) -> tuple[bytes, str]:
    generation_config = {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": voice_name}
            }
        },
    }

    last_error: Exception | None = None
    for model_name in TTS_MODELS_QUEUE:
        tts_model = GeminiModel(gemini_client, model_name)
        for attempt in range(3):
            try:
                logging.info(
                    "[speech][tts] Gemini model=%s voice=%s attempt=%s",
                    model_name,
                    voice_name,
                    attempt + 1,
                )
                response = tts_model.generate_content(text, generation_config=generation_config)
                if not response or not getattr(response, "candidates", None):
                    raise SpeechSynthesisError("Gemini TTS returned no candidates")

                part = response.candidates[0].content.parts[0]
                inline_data = getattr(part, "inline_data", None)
                if not inline_data or not getattr(inline_data, "data", None):
                    raise SpeechSynthesisError("Gemini TTS returned no inline audio")
                return _pcm_to_wav_bytes(inline_data.data), model_name
            except Exception as exc:  # provider errors vary by google SDK version
                last_error = exc
                text_error = str(exc)
                code = getattr(exc, "code", None)
                is_quota = code == 429 or "429" in text_error or "RESOURCE_EXHAUSTED" in text_error.upper()
                is_missing = code == 404 or "404" in text_error

                if is_missing:
                    logging.warning("[speech][tts] Gemini model %s is unavailable: %s", model_name, exc)
                    break
                if is_quota and attempt < 2:
                    delay = 15 * (attempt + 1) + random.uniform(1, 5)
                    logging.warning(
                        "[speech][tts] Gemini quota for %s; retrying after %.1fs",
                        model_name,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                if attempt < 2 and not is_quota:
                    time.sleep(5)
                    continue
                logging.warning("[speech][tts] Gemini model %s failed: %s", model_name, exc)
                break

    raise SpeechSynthesisError(f"Gemini TTS failed: {last_error}")


async def _synthesize_gemini_chunk(text: str, voice_name: str) -> bytes:
    wav_bytes, _model_name = await asyncio.to_thread(_gemini_tts_sync, text, voice_name)
    return wav_bytes


def _groq_tts_sync(text: str, voice_name: str) -> bytes:
    client = groq_ai.client
    if not client:
        raise SpeechSynthesisError("Groq client is not initialized")

    response = client.audio.speech.create(
        model=GROQ_TTS_MODEL,
        input=text,
        voice=voice_name,
        response_format="wav",
    )
    data = response.read()
    if not data:
        raise SpeechSynthesisError("Groq TTS returned empty audio")
    return data


async def _synthesize_groq_chunk(text: str, voice_name: str) -> bytes:
    return await asyncio.to_thread(_groq_tts_sync, text, voice_name)


async def synthesize_speech(
    text: str,
    *,
    provider_order: tuple[str, ...] = ("gemini", "groq"),
    allow_groq_for_cyrillic: bool = False,
) -> SpeechAudio:
    """Synthesize clean speech and return one MP3 suitable for Telegram voice.

    A provider is retried as a whole: if any chunk fails, partial audio is
    discarded and the next provider starts from the full script.  Groq's
    currently configured Orpheus model is English-only; callers must opt in to
    using it for Cyrillic text (the legacy ``упупа скажи`` path does so to keep
    its historical behavior, Radio Upupa does not).
    """
    if not (text or "").strip():
        raise SpeechSynthesisError("Cannot synthesize empty text")

    errors: list[str] = []
    gemini_voice = random.choice(AVAILABLE_GEMINI_VOICES)
    groq_voice = random.choice(GROQ_TTS_VOICES)

    for provider in provider_order:
        try:
            if provider == "gemini":
                chunks = split_text_for_tts(text, GEMINI_SAFE_CHUNK_CHARS)
                wav_chunks = []
                for index, chunk in enumerate(chunks, 1):
                    logging.info("[speech][tts] provider=gemini chunk=%s/%s chars=%s", index, len(chunks), len(chunk))
                    wav_chunks.append(await _synthesize_gemini_chunk(chunk, gemini_voice))
            elif provider == "groq":
                if _contains_cyrillic(text) and not allow_groq_for_cyrillic:
                    raise SpeechSynthesisError("Groq Orpheus is English-only for the configured model")
                chunks = split_text_for_tts(text, GROQ_SAFE_CHUNK_CHARS)
                wav_chunks = []
                for index, chunk in enumerate(chunks, 1):
                    logging.info("[speech][tts] provider=groq chunk=%s/%s chars=%s", index, len(chunks), len(chunk))
                    wav_chunks.append(await _synthesize_groq_chunk(chunk, groq_voice))
            else:
                raise SpeechSynthesisError(f"Unknown TTS provider: {provider}")

            logging.info("[speech][merge] provider=%s chunks=%s", provider, len(wav_chunks))
            mp3 = await asyncio.to_thread(_merge_wav_chunks_to_mp3, wav_chunks)
            if not mp3:
                raise SpeechSynthesisError("Merged speech is empty")
            return SpeechAudio(data=mp3, format="mp3", provider=provider, chunks=len(wav_chunks))
        except Exception as exc:
            logging.warning("[speech][tts] provider=%s failed: %s", provider, exc, exc_info=True)
            errors.append(f"{provider}: {exc}")

    raise SpeechSynthesisError("; ".join(errors) or "No TTS providers configured")
