from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd
from AI import dnd_completion


def test_gemini_session_does_not_eagerly_build_legacy_chat(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dnd,
        "model",
        SimpleNamespace(start_chat=lambda **kwargs: calls.append(kwargs)),
    )

    session = dnd.GameSession(
        -100930,
        active_model="gemini",
        conversation=[
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "ready"},
            {"role": "user", "content": "old turn " + ("x" * 20_000)},
            {"role": "assistant", "content": "old result " + ("y" * 20_000)},
        ],
    )

    assert session.chat_session is None
    assert calls == []


def test_legacy_gemini_chat_is_created_on_first_direct_send(monkeypatch):
    starts = []
    sends = []

    class FakeChat:
        def send_message(self, text, *, chat_id):
            sends.append((text, chat_id))
            return SimpleNamespace(text="legacy ok")

    def start_chat(*, chat_id, history):
        starts.append((chat_id, history))
        return FakeChat()

    monkeypatch.setattr(dnd, "model", SimpleNamespace(start_chat=start_chat))

    session = dnd.GameSession(
        -100931,
        active_model="gemini",
        conversation=[
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "ready"},
        ],
    )

    result = session.send_message("direct legacy call")

    assert result == "legacy ok"
    assert starts == [
        (
            -100931,
            [
                {"role": "user", "parts": ["system"]},
                {"role": "model", "parts": ["ready"]},
            ],
        )
    ]
    assert sends == [("direct legacy call", -100931)]
    assert session.conversation[-2:] == [
        {"role": "user", "content": "direct legacy call"},
        {"role": "assistant", "content": "legacy ok"},
    ]


def test_rewind_does_not_materialize_unused_legacy_gemini_chat(monkeypatch):
    calls = []
    session = SimpleNamespace(
        active_model="gemini",
        chat_id=-100932,
        conversation=[
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "ready"},
            {"role": "user", "content": "half committed request"},
            {"role": "assistant", "content": "half committed response"},
        ],
        chat_session=None,
    )
    monkeypatch.setattr(
        dnd,
        "model",
        SimpleNamespace(start_chat=lambda **kwargs: calls.append(kwargs)),
    )

    changed = dnd._rewind_session_conversation(session, 2)

    assert changed is True
    assert session.conversation == [
        {"role": "user", "content": "system"},
        {"role": "assistant", "content": "ready"},
    ]
    assert session.chat_session is None
    assert calls == []


def test_rewind_rebuilds_legacy_chat_if_already_materialized(monkeypatch):
    starts = []
    rebuilt_chat = object()
    session = SimpleNamespace(
        active_model="gemini",
        chat_id=-100933,
        conversation=[
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "ready"},
            {"role": "user", "content": "half committed request"},
            {"role": "assistant", "content": "half committed response"},
        ],
        chat_session=object(),
    )

    def start_chat(*, chat_id, history):
        starts.append((chat_id, history))
        return rebuilt_chat

    monkeypatch.setattr(dnd, "model", SimpleNamespace(start_chat=start_chat))

    changed = dnd._rewind_session_conversation(session, 2)

    assert changed is True
    assert session.chat_session is rebuilt_chat
    assert starts == [
        (
            -100933,
            [
                {"role": "user", "parts": ["system"]},
                {"role": "model", "parts": ["ready"]},
            ],
        )
    ]


def test_completion_refresh_does_not_materialize_unused_legacy_chat():
    calls = []
    fake_dnd = SimpleNamespace(
        model=SimpleNamespace(start_chat=lambda **kwargs: calls.append(kwargs))
    )
    session = SimpleNamespace(
        active_model="gemini",
        chat_id=-100934,
        conversation=[
            {"role": "user", "content": "system"},
            {"role": "assistant", "content": "ready"},
        ],
        chat_session=None,
    )

    dnd_completion._refresh_gemini_chat_session(fake_dnd, session)

    assert session.chat_session is None
    assert calls == []
