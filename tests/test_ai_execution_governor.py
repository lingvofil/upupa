import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from infrastructure.ai import clients
from infrastructure.ai.execution import (
    AIExecutionGovernor,
    AIQueueTimeoutError,
    AIRequestTimeoutError,
    ai_execution_lane,
)


def _governor(*, maximum=1, background=1, queue=0.05, request=0.2):
    return AIExecutionGovernor(
        max_concurrency=maximum,
        background_max_concurrency=background,
        queue_timeout_seconds=queue,
        request_timeout_seconds=request,
    )


def test_request_timeout_keeps_provider_slot_until_worker_really_finishes():
    governor = _governor(request=0.05, queue=0.03)
    entered = threading.Event()
    release = threading.Event()

    def stuck_call():
        entered.set()
        release.wait(timeout=1)
        return "late"

    try:
        with pytest.raises(AIRequestTimeoutError):
            governor.run("stuck", stuck_call)
        assert entered.is_set()

        # The timed-out SDK call is still alive. Its slot must remain occupied,
        # otherwise repeated requests could create an unbounded thread pile.
        with pytest.raises(AIQueueTimeoutError):
            governor.run("second", lambda: "should-not-run")

        release.set()
        deadline = time.monotonic() + 1
        while governor.snapshot().in_flight and time.monotonic() < deadline:
            time.sleep(0.01)

        assert governor.run("third", lambda: "ok") == "ok"
        snapshot = governor.snapshot()
        assert snapshot.request_timeouts == 1
        assert snapshot.queue_timeouts == 1
        assert snapshot.in_flight == 0
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_background_lane_has_own_limit_without_consuming_all_global_capacity():
    governor = _governor(maximum=2, background=1, queue=0.04, request=0.5)
    entered = threading.Event()
    release = threading.Event()

    def slow_background():
        entered.set()
        release.wait(timeout=1)
        return "background-done"

    def run_background():
        with ai_execution_lane("background"):
            return governor.run("background-1", slow_background)

    callers = ThreadPoolExecutor(max_workers=1)
    first = callers.submit(run_background)
    try:
        assert entered.wait(timeout=0.5)

        # A second background request cannot use the second global slot.
        with ai_execution_lane("background"):
            with pytest.raises(AIQueueTimeoutError):
                governor.run("background-2", lambda: "nope")

        # Interactive work can still use the remaining global capacity.
        assert governor.run("interactive", lambda: "ok") == "ok"
        snapshot = governor.snapshot()
        assert snapshot.in_flight_background == 1
        assert snapshot.in_flight == 1

        release.set()
        assert first.result(timeout=1) == "background-done"
    finally:
        release.set()
        callers.shutdown(wait=True)
        governor.shutdown(wait=True)


def test_lazy_resource_routes_provider_methods_through_governor(monkeypatch):
    calls = []

    def fake_governor(operation, func, *args, **kwargs):
        calls.append((operation, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(clients, "run_ai_provider_call", fake_governor)

    class FakeModels:
        def embed_content(self, *, model, contents):
            return f"{model}:{contents}"

    class FakeProvider:
        def __init__(self):
            self.models = FakeModels()

        def generate_text(self, prompt, max_tokens=10):
            return f"{prompt}:{max_tokens}"

    resource = clients.LazyResource("fake", FakeProvider)
    first_method = resource.generate_text
    second_method = resource.generate_text

    assert first_method is second_method
    assert first_method("hello", max_tokens=7) == "hello:7"
    assert resource.models.embed_content(model="embed", contents="text") == "embed:text"
    assert [call[0] for call in calls] == [
        "fake.generate_text",
        "fake.models.embed_content",
    ]


def test_lazy_resource_wraps_chat_session_send(monkeypatch):
    calls = []

    def fake_governor(operation, func, *args, **kwargs):
        calls.append(operation)
        return func(*args, **kwargs)

    monkeypatch.setattr(clients, "run_ai_provider_call", fake_governor)

    class FakeSession:
        history = ["old"]

        def send_message(self, text):
            return f"reply:{text}"

    class FakeProvider:
        def start_chat(self, **kwargs):
            return FakeSession()

    resource = clients.LazyResource("fake-chat", FakeProvider)
    session = resource.start_chat(history=[])

    assert session.history == ["old"]
    assert session.send_message("ping") == "reply:ping"
    assert calls == ["fake-chat.chat.send_message"]
