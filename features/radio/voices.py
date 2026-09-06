"""Two-voice synthesis for Radio Upupa host and invited expert."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import random
import re

import services.speech as speech


_LABEL_RE = re.compile(r"(?P<label>ВЕДУЩИЙ|ЭКСПЕРТ)\s*:\s*", re.IGNORECASE)


@dataclass(frozen=True)
class SpeakerTurn:
    speaker: str
    text: str


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


async def synthesize_two_voice_radio(script: str) -> speech.SpeechAudio | None:
    """Use two distinct Gemini voices; return None when the script is not dual-speaker."""
    turns = parse_speaker_turns(script)
    if not turns or not any(turn.speaker == "expert" for turn in turns):
        return None

    host_voice, expert_voice = random.sample(speech.AVAILABLE_GEMINI_VOICES, 2)
    wav_chunks: list[bytes] = []
    chunk_count = 0
    for turn in turns:
        voice = host_voice if turn.speaker == "host" else expert_voice
        chunks = speech.split_text_for_tts(turn.text, speech.GEMINI_SAFE_CHUNK_CHARS)
        for chunk in chunks:
            wav_chunks.append(await speech._synthesize_gemini_chunk(chunk, voice))
            chunk_count += 1

    if not wav_chunks:
        return None
    mp3 = await asyncio.to_thread(speech._merge_wav_chunks_to_mp3, wav_chunks)
    return speech.SpeechAudio(
        data=mp3,
        format="mp3",
        provider="gemini-dual",
        chunks=chunk_count,
    )


def strip_speaker_labels(script: str) -> str:
    return _LABEL_RE.sub("", script or "").strip()
