"""Grounded AI classification/generation for Chronicle candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re

from AI.summarize import _generate_with_active_model
from features.chronicle.models import ChronicleCandidate, ChronicleEvent, ChronicleEventDraft
from infrastructure.ai.execution import ai_feature


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(slots=True)
class ChronicleDecision:
    accepted: bool
    draft: ChronicleEventDraft | None
    reason: str
    raw_confidence: float = 0.0


def _json_object(text: str) -> dict:
    match = _JSON_RE.search(text or "")
    if not match:
        raise ValueError("AI response has no JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("AI response is not an object")
    return value


def _clean_list(value, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        text = str(item).strip()
        if text and text not in result:
            result.append(text[:120])
    return result


CONFLICT_PAIRS_RULES = """conflict_pairs заполняй ТОЛЬКО для category=conflict (иначе []).
Это пары оппонентов: кто с кем НАПРЯМУЮ спорил, ругался или наезжал — люди
по разные стороны конфликта. Не включай союзников одной стороны друг с другом,
тех, кто просто присутствовал, шутил, задавал вопросы или комментировал со
стороны, и обсуждаемого человека, если он сам не вступил в перепалку.
Если не уверен в паре — не включай её; лучше [] чем выдуманный конфликт.""".strip()


def _pair_list(value, allowed: set[int]) -> list[list[int]]:
    """Validated, deduplicated [[a, b], ...] with a < b, both in ``allowed``."""
    if not isinstance(value, list):
        return []
    pairs: list[list[int]] = []
    for item in value[:30]:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            left, right = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            continue
        if left == right or left not in allowed or right not in allowed:
            continue
        pair = sorted((left, right))
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def build_prompt(candidate: ChronicleCandidate, context: list[dict]) -> str:
    people = candidate.metadata.get("participants", [])
    allowed_ids = sorted({int(p["id"]) for p in people if isinstance(p, dict) and p.get("id") is not None})
    lines = []
    for row in context:
        timestamp = str(row.get("timestamp", ""))[:19]
        message_id = row.get("message_id")
        user_id = row.get("user_id")
        name = row.get("full_name") or row.get("username") or "Участник"
        text = (row.get("text") or "").replace("\x00", " ").strip()
        if len(text) > 700:
            text = text[:697] + "..."
        lines.append(f"[{timestamp}] message_id={message_id} user_id={user_id} {name}: {text}")

    anchor = candidate.anchor_text.strip()
    if anchor and not any(anchor in line for line in lines):
        lines.append(
            f"[anchor] message_id={candidate.anchor_message_id} "
            f"user_id={candidate.anchor_user_id} {candidate.anchor_display_name}: {anchor[:700]}"
        )

    if candidate.source == "backfill":
        source = "исторического backfill"
    elif candidate.source == "live":
        source = "живого чата"
    else:
        source = (
            f"встроенной механики бота «{candidate.source}» (суд, игра и т.п.). "
            "Событие уже состоялось и описано в [anchor]; реши, войдёт ли оно в историю чата, "
            "а не является ли рядовым эпизодом. Цитаты из [anchor] — это текст бота, не участников"
        )
    return f"""
Ты отбираешь события для долговременной «Летописи» Telegram-чата Упупы.
Это кандидат из {source}. Будь КОНСЕРВАТИВЕН: сохраняй только то, что участники
с ненулевой вероятностью будут вспоминать через несколько недель.

Нельзя додумывать факты, мотивы, отношения, исходы или дословные цитаты.
Используй только контекст ниже. Если контекста мало или событие банальное — reject.

Сигналы кандидата:
score={candidate.score:.2f}
reactions={candidate.reaction_count}
unique_reactors={candidate.unique_reactors}
replies={candidate.reply_count}
participants={candidate.participant_count}
source={candidate.source}
allowed_participant_ids={allowed_ids}

Контекст:
{chr(10).join(lines)}

Верни ТОЛЬКО JSON:
{{
  "save": true/false,
  "confidence": 0.0-1.0,
  "reason": "коротко почему",
  "title": "короткий конкретный заголовок",
  "summary": "1-4 конкретных предложения в живом саркастичном стиле Упупы; мат допустим",
  "category": "funny|absurd|conflict|fail|local_meme|confession|decision|promise|bet|record|game|relationship|other",
  "participant_ids": [только ID из allowed_participant_ids],
  "conflict_pairs": [[ID, ID]],
  "keywords": ["ключевые слова"],
  "entities": ["имена/предметы/темы, явно присутствующие в контексте"]
}}

{CONFLICT_PAIRS_RULES}

Для backfill требование ещё строже: обычный смешной разговор без признаков
закрепившейся истории не сохраняй.
""".strip()


@ai_feature("летопись")
async def classify_candidate(candidate: ChronicleCandidate, context: list[dict]) -> ChronicleDecision:
    try:
        raw = await _generate_with_active_model(build_prompt(candidate, context), str(candidate.chat_id))
        data = _json_object(raw or "")
    except Exception as exc:
        logging.warning("[chronicle] AI classification failed candidate=%s chat=%s: %s", candidate.id, candidate.chat_id, exc)
        return ChronicleDecision(False, None, "ai_error")

    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0)))
    if not bool(data.get("save")):
        return ChronicleDecision(False, None, str(data.get("reason") or "ai_reject")[:200], confidence)

    allowed = set(candidate.participant_ids)
    selected: list[int] = []
    for item in data.get("participant_ids") or []:
        try:
            user_id = int(item)
        except (TypeError, ValueError):
            continue
        if user_id in allowed and user_id not in selected:
            selected.append(user_id)
    if not selected:
        selected = list(candidate.participant_ids)

    title = str(data.get("title") or "").strip()[:160]
    summary = str(data.get("summary") or "").strip()[:1800]
    if not title or not summary:
        return ChronicleDecision(False, None, "ai_missing_text", confidence)

    category = str(data.get("category") or "other").strip().lower()[:40]
    ai_metadata: dict = {"reason": str(data.get("reason") or "")[:300]}
    if category == "conflict":
        # Who fought whom: a conflict raises tension only for these pairs, not
        # for everyone who happened to be in the thread.
        ai_metadata["conflict_pairs"] = _pair_list(data.get("conflict_pairs"), set(selected))
    draft = ChronicleEventDraft(
        title=title,
        summary=summary,
        category=category,
        confidence=confidence,
        participant_ids=selected,
        keywords=_clean_list(data.get("keywords")),
        entities=_clean_list(data.get("entities")),
        ai_metadata=ai_metadata,
    )
    return ChronicleDecision(True, draft, "accepted", confidence)


def build_conflict_pairs_prompt(event: ChronicleEvent, context: list[dict]) -> str:
    people = "\n".join(
        f"- user_id={user_id} {name}" for user_id, name in zip(event.participant_ids, event.participant_names)
    )
    lines = []
    for row in context:
        text = (row.get("text") or "").replace("\x00", " ").strip()
        if len(text) > 500:
            text = text[:497] + "..."
        name = row.get("full_name") or row.get("username") or "Участник"
        lines.append(f"[{str(row.get('timestamp', ''))[:19]}] user_id={row.get('user_id')} {name}: {text}")
    transcript = "\n".join(lines) if lines else "(исходные сообщения недоступны — опирайся на описание)"
    return f"""
Эпизод из «Летописи» Telegram-чата уже классифицирован как конфликт.
Определи, кто в нём с кем на самом деле конфликтовал.

Заголовок: {event.title}
Описание: {event.summary}

Участники эпизода:
{people}

Переписка:
{transcript}

{CONFLICT_PAIRS_RULES}

Верни ТОЛЬКО JSON: {{"conflict_pairs": [[ID, ID]]}}
""".strip()


@ai_feature("летопись")
async def extract_conflict_pairs(event: ChronicleEvent, context: list[dict]) -> list[list[int]] | None:
    """Combatant pairs of an already saved conflict; None on AI failure."""
    try:
        raw = await _generate_with_active_model(build_conflict_pairs_prompt(event, context), str(event.chat_id))
        data = _json_object(raw or "")
    except Exception as exc:
        logging.warning("[chronicle] conflict pairs failed event=%s chat=%s: %s", event.id, event.chat_id, exc)
        return None
    return _pair_list(data.get("conflict_pairs"), {int(item) for item in event.participant_ids})
