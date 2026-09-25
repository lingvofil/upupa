"""Grounded AI classification/generation for Chronicle candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re

from AI.summarize import _generate_with_active_model
from features.chronicle.models import ChronicleCandidate, ChronicleEventDraft
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

    source = "исторического backfill" if candidate.source == "backfill" else "живого чата"
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
  "keywords": ["ключевые слова"],
  "entities": ["имена/предметы/темы, явно присутствующие в контексте"]
}}

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

    draft = ChronicleEventDraft(
        title=title,
        summary=summary,
        category=str(data.get("category") or "other").strip().lower()[:40],
        confidence=confidence,
        participant_ids=selected,
        keywords=_clean_list(data.get("keywords")),
        entities=_clean_list(data.get("entities")),
        ai_metadata={"reason": str(data.get("reason") or "")[:300]},
    )
    return ChronicleDecision(True, draft, "accepted", confidence)
