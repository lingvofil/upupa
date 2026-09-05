import json
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd


class RecordingSupervisor:
    def __init__(self):
        self.names = []

    def start(self, coro, *, name):
        self.names.append(name)
        coro.close()
        return name


def test_upupa_command_reply_is_not_consumed_as_group_action():
    chat_id = -100600
    dnd.dnd_sessions[chat_id] = SimpleNamespace(
        state="WAITING_ACTION",
        action_prompt_message_id=55,
    )
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=SimpleNamespace(message_id=55),
        text="упупа заверши историю",
        caption=None,
    )

    try:
        assert dnd._is_group_action_reply(message) is False
    finally:
        dnd.dnd_sessions.pop(chat_id, None)


def test_restore_resolving_session_reopens_group_turn(monkeypatch, tmp_path):
    chat_id = -100601
    state_path = tmp_path / "dnd_sessions.json"
    state_path.write_text(
        json.dumps({"version": 1, "sessions": [{"chat_id": chat_id}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dnd, "DND_STATE_PATH", state_path)

    class RestoredSession:
        def __init__(self):
            self.chat_id = chat_id
            self.state = "RESOLVING"
            self.current_poll_id = None
            self.pending_poll = None
            self.action_prompt_message_id = None
            self.pending_actions = {}
            self.action_deadline = None

        def to_record(self):
            return {
                "chat_id": self.chat_id,
                "active_model": "groq",
                "conversation": [],
                "state": self.state,
                "last_roll_stat": None,
                "current_poll_id": None,
                "pending_poll": None,
                "action_prompt_message_id": self.action_prompt_message_id,
                "pending_actions": self.pending_actions,
                "action_deadline": self.action_deadline,
                "recent_scene_types": [],
            }

    restored_session = RestoredSession()
    monkeypatch.setattr(
        dnd.GameSession,
        "from_record",
        classmethod(lambda cls, record: restored_session),
    )
    supervisor = RecordingSupervisor()
    monkeypatch.setattr(dnd, "_task_supervisor", supervisor)

    try:
        assert dnd.restore_dnd_sessions(object()) == 1
        assert restored_session.state == "WAITING_ACTION"
        assert supervisor.names == [f"dnd-actions:{chat_id}:restore-prompt"]
    finally:
        dnd.dnd_sessions.clear()
        dnd.poll_map.clear()
