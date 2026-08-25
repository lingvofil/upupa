import ast
import asyncio
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


def test_dynamic_game_tasks_use_configured_supervisor():
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
        def send_message(self, prompt):
            provider_threads.append(threading.get_ident())
            return f"reply:{prompt}"

    result = asyncio.run(dnd.generate_session_response(Session(), "hello"))

    assert result == "reply:hello"
    assert provider_threads
    assert provider_threads[0] != event_loop_thread


def test_dnd_provider_call_has_timeout(monkeypatch):
    class SlowSession:
        def send_message(self, prompt):
            time.sleep(0.05)
            return prompt

    monkeypatch.setattr(dnd, "DND_MODEL_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(dnd.generate_session_response(SlowSession(), "hello"))
