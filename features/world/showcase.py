"""Normalize text and Telegram media shown during a World of Upupa екскурсия."""

from __future__ import annotations

from dataclasses import dataclass


_MEDIA_LABELS = {
    "photo": "фото",
    "video": "видео",
    "animation": "гифка",
    "sticker": "стикер",
    "voice": "войс",
    "audio": "аудио",
    "document": "документ",
    "video_note": "видеосообщение",
}


@dataclass(frozen=True)
class ShowcaseContent:
    text: str
    media_type: str | None = None

    @property
    def is_media(self) -> bool:
        return self.media_type is not None


def _clean(value: object, *, limit: int = 1000) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _detect_media_type(message) -> str | None:
    for media_type in _MEDIA_LABELS:
        if getattr(message, media_type, None):
            return media_type
    return None


def extract_showcase_content(message) -> ShowcaseContent | None:
    """Return a journal-friendly representation of a showcase reply.

    Text is preserved as-is. Media is represented as ``[тип]`` plus its
    caption/emoji/file name when available, so final visit reports remain useful
    even though Telegram file bytes are not stored in the World ledger.
    """
    media_type = _detect_media_type(message)
    text = _clean(getattr(message, "text", None) or getattr(message, "caption", None))

    if media_type is None:
        return ShowcaseContent(text=text) if text else None

    label = _MEDIA_LABELS[media_type]
    detail = text
    if not detail and media_type == "sticker":
        detail = _clean(getattr(getattr(message, "sticker", None), "emoji", None), limit=32)
    if not detail and media_type == "document":
        detail = _clean(getattr(getattr(message, "document", None), "file_name", None), limit=160)

    summary = f"[{label}]"
    if detail:
        summary = f"{summary} {detail}"
    return ShowcaseContent(text=summary[:1000], media_type=media_type)
