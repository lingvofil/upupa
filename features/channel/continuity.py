"""Continuity layer: let Upupa remember and develop its own channel history."""

from __future__ import annotations

import asyncio
from datetime import datetime
import random
import re

from features.channel import chat_context
from features.channel import service as base
from features.channel.mood import mood_prompt
from prompts.channel import CHANNEL_PERSONA


CONTINUITY_PROBABILITY = 0.18
MIN_HISTORY_POSTS = 4
ARC_LOOKBACK = 8


def should_try_continuity(published_posts: list[dict], *, rng=random) -> bool:
    if len(published_posts) < MIN_HISTORY_POSTS:
        return False
    return rng.random() < CONTINUITY_PROBABILITY


def _recent_arc(published_posts: list[dict]) -> dict | None:
    for post in reversed(published_posts[-ARC_LOOKBACK:]):
        if post.get("continuity_arc") and int(post.get("continuity_arc_step") or 0) < 3:
            return post
    return None


def _old_post(published_posts: list[dict], *, rng=random) -> dict | None:
    candidates = [
        post for post in published_posts[-20:-3]
        if str(post.get("text") or "").strip() and post.get("post_kind") != "external_comment"
    ]
    return rng.choice(candidates) if candidates else None


async def _world_context() -> str | None:
    try:
        from features.world.news import build_world_fact_feed
        from features.world.service import get_world_service

        facts = await build_world_fact_feed(get_world_service(), days=7, event_limit=8)
        return facts or None
    except Exception:
        return None


def _sanitize(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[:240].rstrip()


async def prepare_continuity_post(
    published_posts: list[dict],
    mood: dict,
    *,
    rng=random,
) -> tuple[str, dict] | None:
    """Generate a grounded follow-up, mini-arc, recurring rubric or world reaction."""
    from AI.summarize import _generate_with_active_model

    recent = published_posts[-base.RECENT_POSTS_LIMIT:]
    arc = _recent_arc(published_posts)
    mode = "arc" if arc and rng.random() < 0.55 else rng.choice(("memory", "rubric", "correspondent", "world"))
    factual_block = ""
    metadata: dict = {
        "post_kind": "normal",
        "content_mode": "continuity",
        "chat_context_used": False,
        "continuity_mode": mode,
        "mood": mood.get("name"),
        "mood_posts_left": mood.get("posts_left"),
    }

    if mode == "arc" and arc:
        arc_name = str(arc.get("continuity_arc") or "навязчивая идея")[:80]
        step = int(arc.get("continuity_arc_step") or 0) + 1
        factual_block = (
            f"У тебя уже тянется краткая навязчивая идея «{arc_name}». "
            f"Предыдущий пост этой линии: {str(arc.get('text') or '')[:500]}\n"
            "Продолжи её новым маленьким развитием, изменением мнения, нелепой победой или провалом. "
            "Не повторяй предыдущую фразу. Не объявляй номер серии."
        )
        metadata.update({"continuity_arc": arc_name, "continuity_arc_step": step})
    elif mode == "memory":
        old = _old_post(published_posts, rng=rng)
        if old is None:
            return None
        factual_block = (
            "Ты случайно вспомнил собственный старый пост:\n"
            f"«{str(old.get('text') or '')[:600]}»\n"
            "Коротко вернись к этой мысли: можешь передумать, признать провал, развить её или издевательски "
            "прокомментировать себя прошлого. Не притворяйся, что знаешь последствия, если их нет в данных."
        )
        metadata["continuity_source_message_id"] = old.get("message_id")
    elif mode == "correspondent":
        episode = await asyncio.to_thread(chat_context.pick_chat_episode, published_posts)
        if episode is None:
            return None
        factual_block = (
            "Корреспондентская рубрика. Ниже свежий обезличенный фрагмент реальной жизни одного из чатов:\n"
            f"{str(episode.get('fragment') or '')[:3000]}\n"
            "Сделай короткую реакцию/корреспондентскую заметку только по конкретной детали из этого фрагмента. "
            "Не называй чат и людей, не выдумывай продолжение. Не пиши журналистский заголовок."
        )
        metadata.update({
            "chat_context_used": True,
            "chat_context_key": episode.get("key"),
            "chat_context_latest_at": episode.get("latest_at"),
            "chat_context_messages": episode.get("message_count"),
            "chat_context_participants": episode.get("participant_count"),
            "continuity_rubric": "корреспондент",
        })
    elif mode == "world":
        world = await _world_context()
        if not world:
            return None
        factual_block = (
            "Рубрика про Мир Упупы. Вот реальные факты мировой хроники:\n"
            f"{world[:3500]}\n"
            "Отреагируй от первого лица на один факт. Не придумывай причин, последствий или закулисных событий."
        )
        metadata["continuity_rubric"] = "мир упупы"
    else:
        # A recurring light-weight rubric can seed a short arc for the next few posts.
        arc_name = rng.choice(("предмет дня", "маленькое расследование", "личная реформа", "ненужный эксперимент"))
        factual_block = (
            f"Редкая повторяемая рубрика: «{arc_name}». Придумай один конкретный короткий эпизод из собственной "
            "жизни бота, не выдавая выдумку за событие реальных чатов или мира. Это внутренняя комическая игра "
            "Упупы, которую можно продолжить ещё 1–2 раза в будущих постах."
        )
        metadata.update({"continuity_arc": arc_name, "continuity_arc_step": 1, "continuity_rubric": arc_name})

    prompt = f"""{CHANNEL_PERSONA}

ТВОЁ ТЕКУЩЕЕ ВНУТРЕННЕЕ СОСТОЯНИЕ:
{mood_prompt(mood)}
Не называй состояние напрямую.

Сейчас нужен пост с продолжением собственной биографии канала, а не независимая случайная фраза.
{factual_block}

Ограничения: один пост, 1–2 коротких предложения, максимум 35 слов и 240 символов. Без Markdown, хэштегов,
ссылок, служебных пометок и объяснений механики. Не копируй недавние посты.

Недавняя память канала:
{chr(10).join('- ' + str(post.get('text') or '')[:240] for post in recent[-8:])}

Текущий пост:
"""
    text = _sanitize(await _generate_with_active_model(prompt, str(base.SPECIAL_CHAT_ID)) or "")
    reason = base._validate_post(text, recent)
    if reason:
        return None
    return text, metadata
