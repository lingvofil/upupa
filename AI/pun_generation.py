"""Diverse visual pun generation for the `скаламбурь` command.

The legacy implementation asked the active LLM for a single pun and kept only
12 recent results in RAM. That made models collapse to familiar combinations
and forgot the history on every restart. This module generates a batch of
candidates, validates the overlap rule, rejects recent/cliche results and keeps
per-chat history on disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from AI.gigachat_image import generate_gigachat_image


_HISTORY_FILE = Path(__file__).resolve().parent.parent / "pun_history.json"
_HISTORY_LIMIT = 120
_PROMPT_HISTORY_LIMIT = 60
_MIN_OVERLAP = 2
_MAX_ATTEMPTS = 3
_CANDIDATES_PER_ATTEMPT = 10
_HISTORY_LOCK = threading.Lock()

# These are especially sticky model cliches observed in production. They are
# permanently excluded; all other successful results are excluded dynamically
# through the per-chat history.
_CLICHE_RESULTS = {
    "бегемотоцикл",
    "пингвиноград",
}
_CLICHE_PAIRS = {
    "бегемот|мотоцикл",
    "пингвин|виноград",
}

_CATEGORY_PAIRS = [
    "животное + бытовой предмет",
    "животное + профессия",
    "птица + техника",
    "птица + еда или напиток",
    "морское существо + одежда",
    "насекомое + музыкальный инструмент",
    "растение + транспорт",
    "еда + архитектура",
    "еда + профессия",
    "мифическое существо + бытовая техника",
    "космос + животное",
    "спорт + еда",
    "географический объект + предмет",
    "музыкальный инструмент + животное",
    "техника + растение",
    "одежда + животное",
    "профессия + транспорт",
    "рыба + предмет интерьера",
    "сказочный персонаж + еда",
    "инструмент + птица",
]

_LINE_PREFIX_RE = re.compile(r"^\s*(?:(?:[-*•])|(?:\d+[.)]))\s*")
_PUN_RE = re.compile(r"^\s*(.+?)\s*\+\s*(.+?)\s*=\s*(.+?)\s*$")
_LETTERS_RE = re.compile(r"[^a-zа-яё0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class PunCandidate:
    first: str
    second: str
    result: str

    @property
    def line(self) -> str:
        return f"{self.first}+{self.second} = {self.result}"

    @property
    def pair_signature(self) -> str:
        return f"{_normalize(self.first)}|{_normalize(self.second)}"

    @property
    def result_signature(self) -> str:
        return _normalize(self.result)


def _normalize(value: str) -> str:
    return _LETTERS_RE.sub("", value.lower().replace("ё", "е"))


def _longest_overlap(first: str, second: str) -> int:
    left = _normalize(first)
    right = _normalize(second)
    max_len = min(len(left), len(right))
    for size in range(max_len, _MIN_OVERLAP - 1, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def _expected_result(candidate: PunCandidate) -> Optional[str]:
    first = _normalize(candidate.first)
    second = _normalize(candidate.second)
    overlap = _longest_overlap(candidate.first, candidate.second)
    if not overlap:
        return None
    return first + second[overlap:]


def parse_pun_candidates(text: str) -> list[PunCandidate]:
    """Parse `word1+word2 = result` lines returned by an LLM."""
    candidates: list[PunCandidate] = []
    for raw_line in (text or "").splitlines():
        line = _LINE_PREFIX_RE.sub("", raw_line.strip())
        match = _PUN_RE.match(line)
        if not match:
            continue
        first, second, result = (part.strip(" \t\"'`*") for part in match.groups())
        if first and second and result:
            candidates.append(PunCandidate(first=first, second=second, result=result))
    return candidates


def _history_signatures(lines: Iterable[str]) -> tuple[set[str], set[str]]:
    results: set[str] = set()
    pairs: set[str] = set()
    for line in lines:
        parsed = parse_pun_candidates(line)
        if not parsed:
            continue
        candidate = parsed[0]
        results.add(candidate.result_signature)
        pairs.add(candidate.pair_signature)
    return results, pairs


def is_candidate_acceptable(
    candidate: PunCandidate,
    recent_lines: Iterable[str] = (),
) -> bool:
    """Validate the overlap construction and reject repeats/cliches."""
    expected = _expected_result(candidate)
    if not expected or candidate.result_signature != expected:
        return False

    recent_results, recent_pairs = _history_signatures(recent_lines)
    if candidate.result_signature in {_normalize(v) for v in _CLICHE_RESULTS}:
        return False
    if candidate.pair_signature in _CLICHE_PAIRS:
        return False
    if candidate.result_signature in recent_results:
        return False
    if candidate.pair_signature in recent_pairs:
        return False
    return True


def _load_history() -> dict[str, list[str]]:
    if not _HISTORY_FILE.exists():
        return {}
    try:
        with _HISTORY_FILE.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            return {}
        return {
            str(chat_id): [str(line) for line in lines][- _HISTORY_LIMIT:]
            for chat_id, lines in raw.items()
            if isinstance(lines, list)
        }
    except Exception as exc:
        logging.warning("Pun history load failed: %s", exc)
        return {}


def _save_history(history: dict[str, list[str]]) -> None:
    tmp_path = _HISTORY_FILE.with_suffix(".json.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(history, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _HISTORY_FILE)
    except Exception as exc:
        logging.warning("Pun history save failed: %s", exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _get_recent(chat_id: str) -> list[str]:
    with _HISTORY_LOCK:
        return list(_load_history().get(str(chat_id), []))


def _remember(chat_id: str, line: str) -> None:
    with _HISTORY_LOCK:
        history = _load_history()
        chat_history = history.setdefault(str(chat_id), [])
        if line not in chat_history:
            chat_history.append(line)
        history[str(chat_id)] = chat_history[-_HISTORY_LIMIT:]
        _save_history(history)


def _build_prompt(recent_lines: list[str], attempt: int) -> str:
    themes = random.sample(_CATEGORY_PAIRS, k=3)
    recent = recent_lines[-_PROMPT_HISTORY_LIMIT:]
    forbidden_block = "\n".join(f"- {line}" for line in recent) if recent else "- пока пусто"
    seed = random.randint(100000, 999999)

    return (
        f"Случайный seed: {seed}. Попытка: {attempt}.\n"
        "Придумай РОВНО 10 разных русских визуальных каламбуров-гибридов.\n"
        "Правило построения жесткое: конец первого слова должен буквально совпадать "
        f"с началом второго минимум на {_MIN_OVERLAP} буквы. Итоговое слово получается "
        "наложением общей части, а не простой склейкой.\n"
        "Используй преимущественно существительные, конкретные предметы и существ, которые "
        "можно смешно изобразить. Не используй имена собственные.\n"
        "Не копируй типовые интернет-каламбуры и не бери пары из примеров других запросов.\n"
        "Особенно запрещены пары бегемот+мотоцикл и пингвин+виноград.\n"
        f"На этой попытке тяни идеи из разных областей, особенно: {', '.join(themes)}.\n"
        "Каждая строка строго в формате: слово1+слово2 = итоговоеслово\n"
        "Никаких пояснений, нумерации и дополнительного текста.\n\n"
        "Уже использованные варианты в этом чате — НЕ ПОВТОРЯТЬ ни итоговое слово, ни пару:\n"
        f"{forbidden_block}"
    )


async def _generate_candidate_batch(picgeneration_module, prompt: str, chat_id: str) -> str:
    active_model = picgeneration_module.get_active_model(chat_id)

    if active_model == "gigachat":
        return await asyncio.to_thread(
            lambda: picgeneration_module.gigachat_model.generate_content(
                prompt,
                chat_id=int(chat_id),
                temperature=1.0,
                max_tokens=700,
            ).text.strip()
        )
    if active_model == "groq":
        return await asyncio.to_thread(
            lambda: picgeneration_module.groq_ai.generate_text(
                prompt,
                max_tokens=700,
                temperature=1.0,
                presence_penalty=0.8,
            )
        )

    return await asyncio.to_thread(
        lambda: picgeneration_module.model.generate_content(
            prompt,
            chat_id=int(chat_id),
            generation_config={
                "temperature": 1.0,
                "top_p": 0.98,
                "max_output_tokens": 700,
            },
        ).text.strip()
    )


async def choose_diverse_pun(picgeneration_module, chat_id: str) -> Optional[PunCandidate]:
    """Generate batches and randomly select a valid, unseen pun."""
    recent = _get_recent(chat_id)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        prompt = _build_prompt(recent, attempt)
        raw = await _generate_candidate_batch(picgeneration_module, prompt, chat_id)
        parsed = parse_pun_candidates(raw)

        acceptable: list[PunCandidate] = []
        seen_batch_results: set[str] = set()
        seen_batch_pairs: set[str] = set()
        for candidate in parsed:
            if candidate.result_signature in seen_batch_results:
                continue
            if candidate.pair_signature in seen_batch_pairs:
                continue
            if not is_candidate_acceptable(candidate, recent):
                continue
            seen_batch_results.add(candidate.result_signature)
            seen_batch_pairs.add(candidate.pair_signature)
            acceptable.append(candidate)

        logging.info(
            "Pun candidates: attempt=%s parsed=%s acceptable=%s active_model=%s",
            attempt,
            len(parsed),
            len(acceptable),
            picgeneration_module.get_active_model(chat_id),
        )

        if acceptable:
            candidate = random.choice(acceptable)
            _remember(chat_id, candidate.line)
            return candidate

    return None


async def _generate_pun_image(picgeneration_module, candidate: PunCandidate) -> Optional[bytes]:
    visual_prompt = (
        f"A creative surreal visual hybrid combining {candidate.first} and {candidate.second}, "
        "one coherent subject, humorous visual pun, detailed digital art, high resolution, no text"
    )
    prompt_en = await picgeneration_module.translate_to_en(visual_prompt)

    image = await picgeneration_module.pollinations_generate(prompt_en)
    if image:
        logging.info("Pun image generated via Pollinations")
        return image

    image = await generate_gigachat_image(
        f"Смешной визуальный гибрид двух сущностей: {candidate.first} и {candidate.second}. "
        "Они должны быть объединены в один цельный объект. Без текста и надписей."
    )
    if image:
        logging.info("Pun image generated via GigaChat")
        return image

    logging.info("Pun image: trying HuggingFace fallback")
    image = await picgeneration_module.hf_generate(
        prompt_en,
        "black-forest-labs/FLUX.1-schnell",
    )
    if image:
        return image

    logging.info("Pun image: trying Cloudflare fallback")
    return await picgeneration_module.cf_generate_t2i(prompt_en)


def install_into_picgeneration(picgeneration_module) -> None:
    """Patch the legacy public handler before handlers import it."""

    async def handle_pun_image_command(message):
        chat_id = str(message.chat.id)
        await picgeneration_module.bot.send_chat_action(
            chat_id=chat_id,
            action=random.choice(picgeneration_module.actions),
        )
        status = await message.reply("🤔 Придумываю калом бур...")

        try:
            candidate = await choose_diverse_pun(picgeneration_module, chat_id)
            if candidate is None:
                logging.warning("Pun generation produced no valid unseen candidates")
                return await status.edit_text("Я пидорас")

            await status.edit_text("Ща скаламбурю нахуй")
            image = await _generate_pun_image(picgeneration_module, candidate)

            if not image:
                logging.warning("Pun image generation failed in all providers")
                return await status.edit_text(
                    f"Вот тебе калом бур: {candidate.line}\nРисуй сам, раз такой умный."
                )

            path = await asyncio.to_thread(
                picgeneration_module._overlay_text_on_image,
                image,
                candidate.result,
            )
            try:
                await message.reply_photo(picgeneration_module.types.FSInputFile(path))
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            await status.delete()
        except Exception as exc:
            logging.error("Pun error: %s", exc, exc_info=True)
            await status.edit_text("Ашипка блядь")

    picgeneration_module.handle_pun_image_command = handle_pun_image_command
