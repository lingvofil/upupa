"""Local chat court: adjudication plus persistent judicial practice."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re

from AI.chat_recall import _build_dispute_context, _read_chat_log
from AI.summarize import _generate_with_active_model
from core.json_repository import JsonFileRepository
from core.paths import COURT_CASES_PATH
from core.upupa_utils import normalize_upupa_command


_MAX_CONTEXT_MESSAGES = 500
_MAX_PRACTICE_CASES = 10
_store_lock = asyncio.Lock()
_repository = JsonFileRepository(COURT_CASES_PATH)


def _load_cases_sync() -> list[dict]:
    try:
        value = _repository.load()
    except FileNotFoundError:
        return []
    return value if isinstance(value, list) else []


def _save_case_sync(case: dict) -> dict:
    cases = _load_cases_sync()
    next_id = max((int(item.get("case_id", 0)) for item in cases if isinstance(item, dict)), default=0) + 1
    saved = {**case, "case_id": next_id}
    cases.append(saved)
    _repository.save(cases[-2000:])
    return saved


async def _save_case(case: dict) -> dict:
    async with _store_lock:
        return await asyncio.to_thread(_save_case_sync, case)


async def list_chat_cases(chat_id: int, *, limit: int = _MAX_PRACTICE_CASES) -> list[dict]:
    cases = await asyncio.to_thread(_load_cases_sync)
    filtered = [
        item for item in cases
        if isinstance(item, dict)
        and item.get("scope") == "chat"
        and str(item.get("chat_id")) == str(chat_id)
    ]
    return filtered[-max(1, limit):][::-1]


def _claim_from_command(text: str | None) -> str:
    normalized = normalize_upupa_command(text or "")
    tail = normalized.removeprefix("упупа суд").strip()
    return tail[:500]


def _target_text(message) -> str:
    replied = getattr(message, "reply_to_message", None)
    if not replied:
        return ""
    return (replied.text or replied.caption or "").strip()


def _party_names(context: list[dict]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in context:
        name = str(item.get("name") or "").strip()
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            result.append(name)
        if len(result) >= 6:
            break
    return result


def _compact_verdict(text: str, limit: int = 260) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


async def adjudicate_chat_case(message) -> dict | None:
    """Build one local case from the surrounding real conversation and persist the verdict."""
    chat_id = str(message.chat.id)
    target_text = _target_text(message)
    claim = _claim_from_command(message.text)
    messages = await asyncio.to_thread(_read_chat_log, chat_id, limit=_MAX_CONTEXT_MESSAGES)
    context = _build_dispute_context(messages, target_text)

    if not context and target_text and message.reply_to_message and message.reply_to_message.from_user:
        context = [{
            "name": message.reply_to_message.from_user.full_name,
            "text": target_text,
            "dt": datetime.now(),
        }]
    if not context:
        return None

    parties = _party_names(context)
    dialog = "\n".join(f"{item['name']}: {item['text']}" for item in context[-45:])
    claim_block = claim or "Отдельная формулировка иска не указана: восстанови предмет спора только по переписке."
    prompt = f"""Ты — судья Суда Упупы. Ниже дан реальный фрагмент переписки Telegram-чата.

Рассмотри дело как сатирический, но последовательный суд. Ничего не выдумывай сверх переписки.
Определи предмет спора, позиции сторон и вынеси однозначное решение. Если обе стороны несут чушь — так и реши.
В конце назначь короткое комическое наказание или меру пресечения, не предполагающую реального вреда, травли или денег.

Формат ответа, обычным текстом без Markdown:
Суть дела: ...
Доводы: ...
Решение: ...
Наказание: ...

Не более 180 слов. Допустим сарказм и мат.
Заявленная претензия: {claim_block}

Материалы дела:
{dialog}
"""
    verdict = (await _generate_with_active_model(prompt, chat_id) or "").strip()
    if not verdict:
        return None

    requester = message.from_user.full_name if message.from_user else "неизвестный истец"
    target_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    target_name = target_user.full_name if target_user else None
    saved = await _save_case({
        "scope": "chat",
        "chat_id": int(message.chat.id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "requester_id": getattr(message.from_user, "id", None),
        "requester_name": requester,
        "target_name": target_name,
        "parties": parties,
        "claim": claim,
        "verdict": verdict,
    })
    return saved


def format_judicial_practice(cases: list[dict]) -> str:
    if not cases:
        return "⚖️ Судебная практика пока пуста. Даже прецедента с табуреткой нет."
    lines = ["⚖️ Судебная практика Упупы", ""]
    for case in cases:
        try:
            when = datetime.fromisoformat(str(case.get("created_at"))).strftime("%d.%m.%Y")
        except ValueError:
            when = "без даты"
        parties = case.get("parties") or []
        party_text = " ↔ ".join(str(name) for name in parties[:3]) or str(case.get("requester_name") or "участники")
        lines.append(f"Дело №{case.get('case_id')} · {when} · {party_text}")
        lines.append(_compact_verdict(str(case.get("verdict") or "")))
        lines.append("")
    return "\n".join(lines).rstrip()
