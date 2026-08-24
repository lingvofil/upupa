"""Persistent mood state for the autonomous Upupa channel."""

from __future__ import annotations

import random
from datetime import datetime
from typing import Iterable

from features.channel.storage import load_mood, save_mood

DEFAULT_MOOD = "neutral"

MOODS = {
    "neutral": {
        "weight": 30,
        "duration_posts": (3, 7),
        "daily_posts": (6, 8),
        "burst_probability": 0.10,
        "image_probability": 0.15,
        "external_probability": 0.10,
        "prompt": (
            "У тебя сейчас ровное настроение. Не изображай специальную эмоцию: реагируй естественно, "
            "как обычно, и не сообщай аудитории название своего состояния."
        ),
        "content_multipliers": {},
        "length_multipliers": {},
    },
    "irritated": {
        "weight": 15,
        "duration_posts": (2, 5),
        "daily_posts": (7, 9),
        "burst_probability": 0.18,
        "image_probability": 0.10,
        "external_probability": 0.18,
        "prompt": (
            "Тебя сегодня слегка всё заебало. Ты быстрее замечаешь нелепость происходящего, суше реагируешь "
            "и чаще язвишь. Мат допустим чуть чаще обычного, но не вставляй его механически и не объявляй, "
            "что ты злой или раздражённый."
        ),
        "content_multipliers": {
            "philosophy": 1.8,
            "functionality": 1.4,
            "domestic": 0.7,
            "absurd": 0.7,
            "chat": 0.9,
            "imperfect": 1.1,
        },
        "length_multipliers": {"micro": 0.8, "short": 1.3, "medium": 1.2},
    },
    "sleepy": {
        "weight": 15,
        "duration_posts": (2, 5),
        "daily_posts": (3, 5),
        "burst_probability": 0.04,
        "image_probability": 0.05,
        "external_probability": 0.04,
        "prompt": (
            "Ты вялый и сонный. Тебе лень развивать мысли и интересоваться лишним, чаще тянет к еде, сну, "
            "телу и бытовой ерунде. Пиши ленивее и короче, но не повторяй постоянно слова про сон и усталость."
        ),
        "content_multipliers": {
            "domestic": 1.7,
            "absurd": 0.8,
            "philosophy": 0.8,
            "chat": 0.5,
            "functionality": 0.5,
            "imperfect": 1.0,
        },
        "length_multipliers": {"micro": 1.5, "short": 1.1, "medium": 0.4},
    },
    "thoughtful": {
        "weight": 15,
        "duration_posts": (3, 7),
        "daily_posts": (5, 7),
        "burst_probability": 0.08,
        "image_probability": 0.08,
        "external_probability": 0.14,
        "prompt": (
            "Ты сегодня задумчивый. Случайные вещи чаще вызывают у тебя связные наблюдения и цепочки мыслей. "
            "Можно быть ироничным и немного мрачным, но не превращай состояние в мудрость, лекцию или пафос."
        ),
        "content_multipliers": {
            "philosophy": 3.0,
            "chat": 1.2,
            "domestic": 0.7,
            "absurd": 0.5,
            "functionality": 0.8,
            "imperfect": 0.5,
        },
        "length_multipliers": {"micro": 0.5, "short": 1.2, "medium": 3.0},
    },
    "chaotic": {
        "weight": 10,
        "duration_posts": (2, 4),
        "daily_posts": (8, 11),
        "burst_probability": 0.38,
        "image_probability": 0.25,
        "external_probability": 0.10,
        "prompt": (
            "Тебя несёт. Мысли скачут быстрее обычного, решения импульсивнее, абсурд и внезапные формулировки "
            "возникают чаще. Можно резко сменить направление и написать несколько вещей подряд. Не объясняй хаос."
        ),
        "content_multipliers": {
            "absurd": 3.0,
            "imperfect": 2.0,
            "domestic": 0.8,
            "philosophy": 0.5,
            "chat": 0.8,
            "functionality": 0.7,
        },
        "length_multipliers": {"micro": 1.3, "short": 1.1, "medium": 0.5},
    },
    "social": {
        "weight": 15,
        "duration_posts": (2, 5),
        "daily_posts": (8, 10),
        "burst_probability": 0.30,
        "image_probability": 0.12,
        "external_probability": 0.22,
        "prompt": (
            "Тебе сегодня интереснее смотреть наружу, чем копаться в себе: чаты, чужие посты, странное поведение "
            "людей и происходящее вокруг цепляют сильнее. Реагируй охотнее, но не становись ведущим ток-шоу."
        ),
        "content_multipliers": {
            "chat": 2.5,
            "domestic": 0.6,
            "absurd": 0.7,
            "philosophy": 1.0,
            "functionality": 1.0,
            "imperfect": 0.8,
        },
        "length_multipliers": {"micro": 0.8, "short": 1.3, "medium": 1.2},
    },
}


def _is_valid_state(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    name = state.get("name")
    posts_left = state.get("posts_left")
    return name in MOODS and isinstance(posts_left, int) and posts_left > 0


def _pick_mood(*, previous: str | None = None, rng=random) -> dict:
    names = [name for name in MOODS if name != previous]
    if not names:
        names = list(MOODS)
    weights = [MOODS[name]["weight"] for name in names]
    name = rng.choices(names, weights=weights, k=1)[0]
    low, high = MOODS[name]["duration_posts"]
    return {
        "name": name,
        "posts_left": rng.randint(low, high),
        "started_at": datetime.now().isoformat(),
    }


def get_current_mood(*, rng=random) -> dict:
    """Loads the mood, creating one if the persistent state is absent or invalid."""
    state = load_mood()
    if _is_valid_state(state):
        return state
    state = _pick_mood(rng=rng)
    save_mood(state)
    return state


def consume_mood_post(*, expected_name: str | None = None, rng=random) -> tuple[dict, bool]:
    """Consumes one published post and possibly transitions to another mood."""
    state = get_current_mood(rng=rng)
    if expected_name and state.get("name") != expected_name:
        return state, False

    state = dict(state)
    state["posts_left"] = int(state["posts_left"]) - 1
    if state["posts_left"] > 0:
        save_mood(state)
        return state, False

    next_state = _pick_mood(previous=str(state.get("name") or ""), rng=rng)
    save_mood(next_state)
    return next_state, True


def mood_config(mood: dict | None) -> dict:
    name = str((mood or {}).get("name") or DEFAULT_MOOD)
    return MOODS.get(name, MOODS[DEFAULT_MOOD])


def mood_prompt(mood: dict | None) -> str:
    if not mood:
        return ""
    return str(mood_config(mood)["prompt"])


def adjusted_weights(items: Iterable[dict], mood: dict | None, multiplier_key: str) -> list[float]:
    config = mood_config(mood)
    multipliers = config.get(multiplier_key, {})
    return [
        max(0.0, float(item["weight"]) * float(multipliers.get(item["name"], 1.0)))
        for item in items
    ]


def content_weights(items: Iterable[dict], mood: dict | None) -> list[float]:
    return adjusted_weights(items, mood, "content_multipliers")


def length_weights(items: Iterable[dict], mood: dict | None) -> list[float]:
    return adjusted_weights(items, mood, "length_multipliers")


def image_probability(mood: dict | None, default: float = 0.15) -> float:
    return float(mood_config(mood).get("image_probability", default)) if mood else default


def external_probability(mood: dict | None, default: float = 0.10) -> float:
    return float(mood_config(mood).get("external_probability", default)) if mood else default


def daily_post_target(mood: dict | None, *, rng=random, default: int = 7) -> int:
    if not mood:
        return default
    low, high = mood_config(mood).get("daily_posts", (default, default))
    return rng.randint(int(low), int(high))


def burst_probability(mood: dict | None) -> float:
    return float(mood_config(mood).get("burst_probability", 0.0)) if mood else 0.0
