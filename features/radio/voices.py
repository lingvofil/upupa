"""Two-voice synthesis for Radio Upupa host and invited expert."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import random
import re

from core.settings import GEMINI_KEYS_POOL, TTS_MODELS_QUEUE
from infrastructure.ai.clients import LazyResource
from infrastructure.ai.gemini import ModelFallbackWrapper
import services.speech as speech


_LABEL_RE = re.compile(r"(?P<label>ВЕДУЩИЙ|ЭКСПЕРТ)\s*:\s*", re.IGNORECASE)

# A full ~3 minute episode can exceed the Gemini transport deadline when it is
# synthesized in one request. Keep each request around a short spoken segment,
# while still making far fewer calls than the old per-speaker-turn pipeline.
RADIO_TTS_CHUNK_CHARS = 800


@dataclass(frozen=True)
class SpeakerTurn:
    speaker: str
    text: str


class RadioTTSQuotaError(speech.SpeechSynthesisError):
    """All Gemini keys available to the shared fallback pool are quota-exhausted."""


def _build_radio_gemini_pool() -> ModelFallbackWrapper:
    """Build one persistent TTS-only pool so key round-robin survives chunks."""
    return ModelFallbackWrapper(
        list(TTS_MODELS_QUEUE),
        list(TTS_MODELS_QUEUE),
        keys_pool=GEMINI_KEYS_POOL,
    )


radio_gemini_model = LazyResource("radio_tts_model", _build_radio_gemini_pool)


def parse_speaker_turns(script: str) -> tuple[SpeakerTurn, ...]:
    """Parse inline speaker labels without making them audible."""
    text = (script or "").strip()
    matches = list(_LABEL_RE.finditer(text))
    if not matches:
        return ()
    turns: list[SpeakerTurn] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            label = match.group("label").upper()
            turns.append(SpeakerTurn("expert" if label == "ЭКСПЕРТ" else "host", content))
    return tuple(turns)


def _looks_like_quota_error(error: Exception) -> bool:
    text = str(error).lower()
    return (
        "лимиты gemini временно исчерпаны" in text
        or "resource_exhausted" in text
        or "quota" in text
        or "429" in text
    )


def _extract_radio_wav(response) -> bytes:
    if not response or not getattr(response, "candidates", None):
        raise speech.SpeechSynthesisError("Gemini radio TTS returned no candidates")
    parts = getattr(response.candidates[0].content, "parts", None) or []
    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data and getattr(inline_data, "data", None):
            return speech._pcm_to_wav_bytes(inline_data.data)
    raise speech.SpeechSynthesisError("Gemini radio TTS returned no inline audio")


def _generate_radio_tts_sync(text: str, speech_config: dict) -> bytes:
    """Generate one bounded radio chunk through a persistent Gemini key pool."""
    try:
        logging.info(
            "[radio][tts] pooled Gemini models=%s chars=%s",
            ",".join(TTS_MODELS_QUEUE),
            len(text),
        )
        response = radio_gemini_model.generate_content(
            text,
            generation_config={
                "response_modalities": ["AUDIO"],
                "speech_config": speech_config,
            },
            require_text=False,
        )
        return _extract_radio_wav(response)
    except Exception as exc:
        logging.warning("[radio][tts] pooled Gemini chunk failed: %s", exc)
        if _looks_like_quota_error(exc):
            raise RadioTTSQuotaError(f"Gemini radio TTS quota exhausted: {exc}") from exc
        raise speech.SpeechSynthesisError(f"Gemini radio TTS failed: {exc}") from exc


def _dual_voice_chunks(turns: tuple[SpeakerTurn, ...]) -> list[str]:
    """Split labelled dialogue while repeating speaker labels in every chunk."""
    segments: list[str] = []
    for turn in turns:
        label = "HOST" if turn.speaker == "host" else "EXPERT"
        prefix = f"{label}: "
        max_text_chars = RADIO_TTS_CHUNK_CHARS - len(prefix)
        for part in speech.split_text_for_tts(turn.text, max_text_chars):
            segments.append(prefix + part)

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for segment in segments:
        extra = len(segment) + (1 if current else 0)
        if current and current_chars + extra > RADIO_TTS_CHUNK_CHARS:
            chunks.append("\n".join(current))
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += len(segment) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _synthesize_radio_chunks(
    chunks: list[str],
    speech_config: dict,
    *,
    provider: str,
) -> speech.SpeechAudio:
    if not chunks:
        raise speech.SpeechSynthesisError("Cannot synthesize empty radio script")

    wav_chunks: list[bytes] = []
    for index, chunk in enumerate(chunks, 1):
        logging.info(
            "[radio][tts] provider=%s chunk=%s/%s chars=%s",
            provider,
            index,
            len(chunks),
            len(chunk),
        )
        wav_chunks.append(
            await asyncio.to_thread(_generate_radio_tts_sync, chunk, speech_config)
        )

    mp3 = await asyncio.to_thread(speech._merge_wav_chunks_to_mp3, wav_chunks)
    return speech.SpeechAudio(
        data=mp3,
        format="mp3",
        provider=provider,
        chunks=len(wav_chunks),
    )


async def synthesize_two_voice_radio(script: str) -> speech.SpeechAudio | None:
    """Synthesize host/expert dialogue in bounded multi-speaker chunks."""
    turns = parse_speaker_turns(script)
    if not turns or not any(turn.speaker == "expert" for turn in turns):
        return None

    host_voice, expert_voice = random.sample(speech.AVAILABLE_GEMINI_VOICES, 2)
    speech_config = {
        "language_code": "ru-RU",
        "multi_speaker_voice_config": {
            "speaker_voice_configs": [
                {
                    "speaker": "HOST",
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": host_voice}
                    },
                },
                {
                    "speaker": "EXPERT",
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": expert_voice}
                    },
                },
            ]
        },
    }
    return await _synthesize_radio_chunks(
        _dual_voice_chunks(turns),
        speech_config,
        provider="gemini-dual",
    )


async def synthesize_single_voice_radio(script: str) -> speech.SpeechAudio:
    """Single-voice fallback using the same bounded pooled Gemini chunks."""
    text = strip_speaker_labels(script)
    chunks = speech.split_text_for_tts(text, RADIO_TTS_CHUNK_CHARS)
    if not chunks:
        raise speech.SpeechSynthesisError("Cannot synthesize empty radio script")

    voice = random.choice(speech.AVAILABLE_GEMINI_VOICES)
    speech_config = {
        "language_code": "ru-RU",
        "voice_config": {
            "prebuilt_voice_config": {"voice_name": voice}
        },
    }
    return await _synthesize_radio_chunks(
        chunks,
        speech_config,
        provider="gemini",
    )


def strip_speaker_labels(script: str) -> str:
    return _LABEL_RE.sub("", script or "").strip()
