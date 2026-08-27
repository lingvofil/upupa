import ast
import asyncio
import json
from pathlib import Path
import threading
import time

import pytest

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from games import crocodile


ROOT = Path(__file__).resolve().parents[1]


class RecordingSupervisor:
    def __init__(self):
        self.names = []

    def start(self, coro, *, name):
        self.names.append(name)
        coro.close()
        return name


def test_dynamic_game_tasks_use_configured_supervisor(monkeypatch):
    monkeypatch.setattr(dnd, "_task_supervisor", None)
    monkeypatch.setattr(crocodile, "_task_supervisor", None)
    supervisor = RecordingSupervisor()
    dnd.configure_task_supervisor(supervisor)
    crocodile.configure_task_supervisor(supervisor)

    async def worker():
        return None

    assert dnd._start_background_task(worker(), name="dnd-test") == "dnd-test"
    assert crocodile._start_background_task(worker(), name="crocodile-test") == "crocodile-test"
    assert supervisor.names == ["dnd-test", "crocodile-test"]


def test_dynamic_modules_do_not_create_unowned_tasks():
    violations = []
    for relative_path in ("AI/dnd.py", "games/crocodile.py"):
        path = ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "asyncio"
                and func.attr == "create_task"
            ):
                violations.append(f"{relative_path}:{node.lineno}")

    assert not violations, "Dynamic tasks must be owned by TaskSupervisor: " + ", ".join(violations)


def test_dnd_provider_call_runs_outside_event_loop_thread():
    event_loop_thread = threading.get_ident()
    provider_threads = []

    class Session:
        chat_id = -1001

        def send_message(self, prompt):
            provider_threads.append(threading.get_ident())
            return f"reply:{prompt}"

    result = asyncio.run(dnd.generate_session_response(Session(), "hello"))

    assert result == "reply:hello"
    assert provider_threads
    assert provider_threads[0] != event_loop_thread


def test_dnd_provider_call_has_timeout(monkeypatch):
    class SlowSession:
        chat_id = -1002

        def send_message(self, prompt):
            time.sleep(0.05)
            return prompt

    monkeypatch.setattr(dnd, "DND_MODEL_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(dnd.generate_session_response(SlowSession(), "hello"))


def test_dnd_poll_timeout_finalizes_even_without_poll_answer(monkeypatch):
    chat_id = -100123
    poll_id = "poll-1"
    options = ["Налево", "Направо"]
    calls = []

    class Session:
        current_poll_id = poll_id

    async def fake_sleep(delay):
        calls.append(("sleep", delay))

    async def fake_finalize(bot, resolved_chat_id, message_id, resolved_options):
        calls.append(
            ("finalize", resolved_chat_id, message_id, tuple(resolved_options))
        )

    monkeypatch.setattr(dnd.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(dnd, "finalize_poll", fake_finalize)
    dnd.dnd_sessions[chat_id] = Session()

    try:
        asyncio.run(
            dnd.wait_for_poll_timeout(
                object(),
                chat_id,
                chat_id,
                77,
                options,
                poll_id,
            )
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == [
        ("sleep", dnd.DND_POLL_TIMEOUT_SECONDS),
        ("finalize", chat_id, 77, tuple(options)),
    ]


def test_dnd_poll_timeout_ignores_stale_poll(monkeypatch):
    chat_id = -100124
    calls = []

    class Session:
        current_poll_id = "new-poll"

    async def fake_sleep(delay):
        return None

    async def fake_finalize(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(dnd.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(dnd, "finalize_poll", fake_finalize)
    dnd.dnd_sessions[chat_id] = Session()

    try:
        asyncio.run(
            dnd.wait_for_poll_timeout(
                object(),
                chat_id,
                chat_id,
                78,
                ["A", "B"],
                "old-poll",
            )
        )
    finally:
        dnd.dnd_sessions.pop(chat_id, None)

    assert calls == []


def test_dnd_state_is_persisted_atomically(monkeypatch, tmp_path):
    state_path = tmp_path / "dnd_sessions.json"
    monkeypatch.setattr(dnd, "DND_STATE_PATH", state_path)

    class Session:
        def to_record(self):
            return {
                "chat_id": -100200,
                "active_model": "gemini",
                "conversation": [{"role": "user", "content": "контекст"}],
                "state": "WAITING_ACTION",
                "last_roll_stat": None,
                "current_poll_id": None,
                "pending_poll": None,
            }

    dnd.dnd_sessions[-100200] = Session()
    try:
        dnd.persist_dnd_sessions()
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    finally:
        dnd.dnd_sessions.pop(-100200, None)

    assert payload["version"] == 1
    assert payload["sessions"][0]["chat_id"] == -100200
    assert payload["sessions"][0]["conversation"][0]["content"] == "контекст"
    assert not state_path.with_suffix(".json.tmp").exists()


def test_dnd_restore_rebuilds_poll_mapping_and_timer(monkeypatch, tmp_path):
    chat_id = -100201
    poll_id = "restored-poll"
    state_path = tmp_path / "dnd_sessions.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [
                    {
                        "chat_id": chat_id,
                        "active_model": "gemini",
                        "conversation": [],
                        "state": "WAITING_POLL",
                        "last_roll_stat": None,
                        "current_poll_id": poll_id,
                        "pending_poll": {
                            "poll_id": poll_id,
                            "poll_chat_id": chat_id,
                            "message_id": 99,
                            "options": ["A", "B"],
                            "deadline": 1050.0,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dnd, "DND_STATE_PATH", state_path)
    monkeypatch.setattr(dnd.time, "time", lambda: 1000.0)

    class RestoredSession:
        def __init__(self):
            self.chat_id = chat_id
            self.state = "WAITING_POLL"
            self.current_poll_id = poll_id
            self.pending_poll = {
                "poll_id": poll_id,
                "poll_chat_id": chat_id,
                "message_id": 99,
                "options": ["A", "B"],
                "deadline": 1050.0,
            }

        def to_record(self):
            return {
                "chat_id": self.chat_id,
                "active_model": "gemini",
                "conversation": [],
                "state": self.state,
                "last_roll_stat": None,
                "current_poll_id": self.current_poll_id,
                "pending_poll": self.pending_poll,
            }

    monkeypatch.setattr(
        dnd.GameSession,
        "from_record",
        classmethod(lambda cls, record: RestoredSession()),
    )
    supervisor = RecordingSupervisor()
    monkeypatch.setattr(dnd, "_task_supervisor", supervisor)

    try:
        restored = dnd.restore_dnd_sessions(object())
        assert restored == 1
        assert dnd.dnd_sessions[chat_id].current_poll_id == poll_id
        assert dnd.poll_map[poll_id] == chat_id
        assert supervisor.names == [f"dnd-poll:{chat_id}:{poll_id}:restored"]
    finally:
        dnd.dnd_sessions.clear()
        dnd.poll_map.clear()
