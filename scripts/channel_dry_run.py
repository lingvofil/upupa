#!/usr/bin/env python3
"""Прогон постов канала без публикации: настоящие промпты и модель, поддельный Telegram.

Запускать из отдельной копии кода, потому что статистика моделей пишется в DATA_DIR этой копии:

    python scripts/channel_dry_run.py --count 40 --data-dir /root/upupa --chat-log /tmp/tail.log

Из --data-dir только читаются файлы состояния канала; вся их запись идёт во временную папку.
Картинки и голосовые не генерируются: выводятся только их замысел и текст.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE_FILES = (
    "channel_posts.json",
    "channel_mood.json",
    "channel_polls.json",
    "channel_external_mentions.json",
    "channel_reposts.json",
)


class DryRunBot:
    """Вместо Telegram только присваивает постам номера."""

    def __init__(self) -> None:
        self.next_id = 1_000_000

    def _sent(self) -> SimpleNamespace:
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)

    async def send_message(self, _target, _text, **_kwargs):
        return self._sent()

    async def send_photo(self, _target, _photo, **_kwargs):
        return self._sent()

    async def send_voice(self, _target, **_kwargs):
        return self._sent()

    async def send_poll(self, _target, **_kwargs):
        return SimpleNamespace(message_id=self._sent().message_id, poll=SimpleNamespace(id="dry-run"))


def _redirect_state(sandbox: Path, data_dir: Path) -> None:
    from features.channel import external_mentions, polls, reposts, storage

    for name in STATE_FILES:
        source = data_dir / name
        if source.exists():
            shutil.copy(source, sandbox / name)
    storage.POSTS_FILE = sandbox / "channel_posts.json"
    storage.MOOD_FILE = sandbox / "channel_mood.json"
    storage.SCHEDULE_FILE = sandbox / "channel_schedule.json"
    polls.POLL_STATE_FILE = sandbox / "channel_polls.json"
    external_mentions.MENTION_STATE_FILE = sandbox / "channel_external_mentions.json"
    reposts.REPOSTS_FILE = sandbox / "channel_reposts.json"


def _use_chat_log(chat_log: Path | None, data_dir: Path) -> None:
    from AI import chat_recall
    from core.state import chat_list

    chats_file = data_dir / "chats.json"
    if chats_file.exists():
        chat_list[:] = json.loads(chats_file.read_text(encoding="utf-8"))
    if chat_log is not None:
        chat_recall.LOG_FILE = str(chat_log)
        chat_recall.get_history_repository = lambda *_args, **_kwargs: None


def _skip_expensive_media() -> None:
    from features.channel import image_generation
    import services.speech

    async def no_image(_prompt):
        return b"dry-run", "dry-run"

    async def no_overlay(image_bytes, _text):
        return image_bytes

    async def no_speech(_text, **_kwargs):
        return SimpleNamespace(data=b"dry-run", provider="dry-run", chunks=1)

    image_generation.generate_channel_image = no_image
    image_generation.overlay_channel_text = no_overlay
    services.speech.synthesize_speech = no_speech


def _format_post(post: dict) -> str:
    kind = post.get("post_kind")
    mode = post.get("character_thread") or post.get("continuity_mode") or post.get("content_mode") or "-"
    source = f" {post['external_source_channel']}" if post.get("external_source_channel") else ""
    text = str(post.get("text") or "").replace("\n", " / ")
    if post.get("image_prompt"):
        text += f"  [КАРТИНКА: {post['image_prompt'][:140]}]"
    if post.get("poll_options"):
        text += f"  {post['poll_options']}"
    return (
        f"{kind}/{mode}/{post.get('length_mode') or '-'}/{post.get('mood') or '-'}"
        f"{' CHAT' if post.get('chat_context_used') else ''}{source}: {text}"
    )


async def _run(count: int) -> int:
    from features.channel import cringedep_service, storage

    bot = DryRunBot()
    failures = 0
    for index in range(1, count + 1):
        known = len(storage.load_posts())
        try:
            await cringedep_service.publish_channel_post(bot, source="dry-run")
        except Exception as exc:
            failures += 1
            print(f"{index:02d}. НЕ ПОЛУЧИЛОСЬ: {exc}", flush=True)
            continue
        for post in storage.load_posts()[known:]:
            print(f"{index:02d}. {_format_post(post)}", flush=True)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--data-dir", type=Path, default=ROOT)
    parser.add_argument("--chat-log", type=Path, default=None, help="Копия user_messages.log для постов из чатов")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="upupa-channel-dry-run-") as sandbox:
        _redirect_state(Path(sandbox), args.data_dir)
        _use_chat_log(args.chat_log, args.data_dir)
        _skip_expensive_media()
        failures = asyncio.run(_run(args.count))
    print(f"Готово: {args.count} попыток, неудач {failures}. В Telegram ничего не отправлялось.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
