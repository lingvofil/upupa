"""Защита от мёртвых моделей в очередях.

Google отключает модели поколениями (gemini-2.0-* умерли 01.06.2026,
imagen — 24.06.2026). Мёртвая модель в очереди = минуты пустых ретраев
на каждый фоллбэк. Если тест упал — обнови очередь в core/settings.py.
"""
import time

from tests import test_smoke_imports  # noqa: F401  (env + моки)

DEAD_MODEL_PREFIXES = ("gemini-2.0", "gemini-1.5", "gemini-1.0", "imagen-")


def _assert_alive(queue):
    for name in queue:
        bare = name.removeprefix("models/")
        assert not bare.startswith(DEAD_MODEL_PREFIXES), f"мёртвая модель в очереди: {name}"


def test_gemini_queues_have_no_dead_models():
    from core import settings
    _assert_alive(settings.MODEL_QUEUE_DEFAULT)
    _assert_alive(settings.MODEL_QUEUE_SPECIAL)
    _assert_alive(settings.TTS_MODELS_QUEUE)
    _assert_alive([settings.TEXT_GENERATION_MODEL_LIGHT])
    assert settings.MODEL_QUEUE_DEFAULT, "очередь по умолчанию пуста"


def test_throttle_is_per_key(monkeypatch):
    """Разные ключи не блокируют друг друга; один ключ — выдерживает паузу."""
    from AI import wrapper
    monkeypatch.setattr(wrapper, "PER_KEY_MIN_DELAY", 0.3)
    wrapper._last_call_ts.clear()

    t0 = time.monotonic()
    wrapper._throttle_key("key_a")
    wrapper._throttle_key("key_b")
    assert time.monotonic() - t0 < 0.2, "разные ключи не должны ждать друг друга"

    t1 = time.monotonic()
    wrapper._throttle_key("key_a")  # повтор того же ключа — обязан подождать
    assert time.monotonic() - t1 >= 0.25, "повторный вызов по ключу должен троттлиться"
