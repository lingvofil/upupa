import asyncio
from types import SimpleNamespace

from AI import dnd_combat as combat
from AI.dnd_roll_feedback import WRONG_ROLL_MESSAGE, install_dnd_roll_feedback


class FakeMessage:
    def __init__(self, user_id=2):
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


def test_wrong_attack_roll_feedback_does_not_suggest_player_died(monkeypatch):
    downstream_calls = []

    async def downstream(_dnd, _message, _session):
        downstream_calls.append(True)

    monkeypatch.setattr(combat, "_resolve_player_roll", downstream)
    monkeypatch.setattr(combat, "_upupa_dnd_roll_feedback_installed", False, raising=False)

    dnd = SimpleNamespace(_can_user_act=lambda _session, _user_id, _targets: False)
    session = SimpleNamespace(
        pending_roll={"type": "ATTACK", "target_user_ids": [1]},
    )
    message = FakeMessage()

    install_dnd_roll_feedback(dnd)
    asyncio.run(combat._resolve_player_roll(dnd, message, session))

    assert message.answers == [WRONG_ROLL_MESSAGE]
    assert message.answers == ["Этот бросок не твой."]
    assert downstream_calls == []
