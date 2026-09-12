"""Two-voice synthesis for Radio Upupa host and invited expert."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import random
import re

from core.settings import TTS_MODELS_QUEUE
from infrastructure.ai.clients import model as gemini_model
import services.speech as speech


_LABEL_RE = re.compile(r"(?P<label>ВЕДУЩИЙ|ЭКСПЕРТ)\s*:\s*", re.IGNORECASE)


@dataclass(frozen=True)
class SpeakerTurn:
    speaker: str
    text: str


class RadioTTSQuotaError(speech.SpeechSynthesisError):
    """All Gemini keys available to the shared fallback pool are quota-exhausted."""


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
    """Generate one radio audio chunk through the shared Gemini key pool."""
    last_error: Exception | None = None
    quota_only = True
    generation_config = {
        "response_modalities": ["AUDIO"],
        "speech_config": speech_config,
    }

    for model_name in TTS_MODELS_QUEUE:
        try:
            logging.info("[radio][tts] pooled Gemini model=%s", model_name)
            response = gemini_model.generate_custom(
                model_name,
                text,
                generation_config=generation_config,
                require_text=False,
            )
            return _extract_radio_wav(response)
        except Exception as exc:
            last_error = exc
            quota_only = quota_only and _looks_like_quota_error(exc)
            logging.warning("[radio][tts] pooled Gemini model=%s failed: %s", model_name, exc)

    if last_error is not None and quota_only:
        raise RadioTTSQuotaError(f"Gemini radio TTS quota exhausted: {last_error}")
    raise speech.SpeechSynthesisError(f"Gemini radio TTS failed: {last_error}")


async def synthesize_two_voice_radio(script: str) -> speech.SpeechAudio | None:
    """Synthesize the whole host/expert dialogue in one multi-speaker request."""
    turns = parse_speaker_turns(script)
    if not turns or not any(turn.speaker == "expert" for turn in turns):
        return None

    host_voice, expert_voice = random.sample(speech.AVAILABLE_GEMINI_VOICES, 2)
    transcript = "\n".join(
        f"{'HOST' if turn.speaker == 'host' else 'EXPERT'}: {turn.text}"
        for turn in turns
    )
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
    wav = await asyncio.to_thread(_generate_radio_tts_sync, transcript, speech_config)
    mp3 = await asyncio.to_thread(speech._merge_wav_chunks_to_mp3, [wav])
    return speech.SpeechAudio(data=mp3, format="mp3", provider="gemini-dual", chunks=1)


async def synthesize_single_voice_radio(script: str) -> speech.SpeechAudio:
    """Single-voice radio fallback that still uses the shared Gemini key pool."""
    text = strip_speaker_labels(script)
    if not text:
        raise speech.SpeechSynthesisError("Cannot synthesize empty radio script")

    voice = random.choice(speech.AVAILABLE_GEMINI_VOICES)
    speech_config = {
        "language_code": "ru-RU",
        "voice_config": {
            "prebuilt_voice_config": {"voice_name": voice}
        },
    }
    wav = await asyncio.to_thread(_generate_radio_tts_sync, text, speech_config)
    mp3 = await asyncio.to_thread(speech._merge_wav_chunks_to_mp3, [wav])
    return speech.SpeechAudio(data=mp3, format="mp3", provider="gemini", chunks=1)


def strip_speaker_labels(script: str) -> str:
    return _LABEL_RE.sub("", script or "").strip()
