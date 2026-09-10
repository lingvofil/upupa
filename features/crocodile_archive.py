"""Bounded on-disk gallery for completed Crocodile drawings."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from aiogram.types import BufferedInputFile, InputMediaPhoto

from core.paths import CROCODILE_STATE_PATH


GALLERY_DIR = Path(CROCODILE_STATE_PATH).with_name("crocodile_gallery")
MANIFEST_PATH = GALLERY_DIR / "manifest.json"
MAX_DRAWINGS_PER_CHAT = 60
MAX_DRAWINGS_TOTAL = 300

_lock = asyncio.Lock()


def _load() -> list[dict]:
    try:
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(rows: list[dict]) -> None:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


def _trim(rows: list[dict]) -> list[dict]:
    kept: list[dict] = []
    per_chat: dict[str, int] = {}
    for row in reversed(rows):
        cid = str(row.get("chat_id"))
        if per_chat.get(cid, 0) >= MAX_DRAWINGS_PER_CHAT:
            continue
        per_chat[cid] = per_chat.get(cid, 0) + 1
        kept.append(row)
        if len(kept) >= MAX_DRAWINGS_TOTAL:
            break
    kept.reverse()
    return kept


def _record_sync(
    chat_id: int | str,
    image: bytes,
    word: str,
    artists: list[str],
    mode: str,
) -> None:
    if not image:
        return
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{int(time.time())}-{uuid.uuid4().hex[:10]}.jpg"
    path = GALLERY_DIR / filename
    path.write_bytes(bytes(image))

    rows = _load()
    rows.append(
        {
            "chat_id": str(chat_id),
            "file": filename,
            "word": str(word or ""),
            "artists": [str(name) for name in artists if str(name).strip()],
            "mode": str(mode or "classic"),
            "created_at": int(time.time()),
        }
    )
    trimmed = _trim(rows)
    live_files = {str(row.get("file")) for row in trimmed}
    for row in rows:
        old = str(row.get("file") or "")
        if old and old not in live_files:
            try:
                (GALLERY_DIR / old).unlink(missing_ok=True)
            except OSError:
                pass
    _save(trimmed)


async def record_drawing(
    chat_id: int | str,
    image: bytes | bytearray | None,
    word: str,
    artists: list[str],
    mode: str = "classic",
) -> None:
    if not isinstance(image, (bytes, bytearray)) or not image:
        return
    async with _lock:
        await asyncio.to_thread(
            _record_sync, chat_id, bytes(image), word, list(artists), mode
        )


def _gallery_rows(chat_id: int | str, limit: int = 10) -> list[dict]:
    rows = [row for row in _load() if str(row.get("chat_id")) == str(chat_id)]
    return rows[-max(1, min(int(limit), 10)):]


async def send_gallery(message, limit: int = 10) -> None:
    rows = await asyncio.to_thread(_gallery_rows, message.chat.id, limit)
    valid: list[tuple[dict, bytes]] = []
    for row in rows:
        try:
            image = await asyncio.to_thread((GALLERY_DIR / row["file"]).read_bytes)
        except (OSError, KeyError):
            continue
        valid.append((row, image))

    if not valid:
        await message.answer("🖼 Галерея пока пустая. Сначала хоть что-нибудь нарисуйте.")
        return

    media = []
    for row, image in valid:
        artists = " + ".join(row.get("artists") or ["неизвестный хуйдожник"])
        word = row.get("word") or "?"
        caption = f"🎨 {artists}\nСлово: {word}"
        media.append(
            InputMediaPhoto(
                media=BufferedInputFile(image, filename="crocodile.jpg"),
                caption=caption,
            )
        )
    await message.answer_media_group(media)
