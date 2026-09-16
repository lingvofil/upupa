import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd, dnd_campaign
from AI.dnd_party_history import (
    DndPartyHistoryMiddleware,
    is_party_history_command,
    render_party_history,
)


CHAT_ID = -100975


def _set_archive(monkeypatch):
    archive = {
        "version": 1,
        "chats": {
            str(CHAT_ID): {
                "players": {},
                "campaigns": [
                    {
                        "completed_at": "2026-09-13T18:20:00+00:00",
                        "selected_plot": "Старый почтамт объявил войну адресатам",
                        "finale": "Письма победили, а партия ушла через окно.",
                        "epilogue": "Герои получили пожизненную подписку на спам.",
                        "scenes": ["Почтамт окончательно развалился."],
                    },
                    {
                        "completed_at": "2026-09-16T20:05:00+00:00",
                        "selected_plot": "Санаторий снова потерял этаж",
                        "finale": "Лифт признал поражение. [ACTION:END]",
                        "epilogue": "Санаторий устоял, герои унесли ключ и дурную славу.",
                        "scenes": ["Все выбрались на крышу."],
                    },
                ],
            }
        },
    }
    monkeypatch.setattr(dnd_campaign, "_archive", archive)
    monkeypatch.setattr(dnd_campaign, "_archive_loaded", True)
    return archive


def test_party_history_command_accepts_punctuation():
    assert is_party_history_command("днд партии")
    assert is_party_history_command(" ДНД   ПАРТИИ!!! ")
    assert not is_party_history_command("днд партия")
    assert not is_party_history_command("днд сюжет")


def test_render_party_history_includes_existing_archive_newest_first(monkeypatch):
    _set_archive(monkeypatch)

    text = render_party_history(dnd, CHAT_ID)

    assert "В архиве: 2" in text
    assert "16.09.2026 — Санаторий снова потерял этаж" in text
    assert "13.09.2026 — Старый почтамт объявил войну адресатам" in text
    assert text.index("16.09.2026") < text.index("13.09.2026")
    assert "Санаторий устоял, герои унесли ключ и дурную славу" in text
    assert "пожизненную подписку на спам" in text
    assert "[ACTION:END]" not in text


def test_render_party_history_uses_finale_then_scene_as_fallback(monkeypatch):
    archive = _set_archive(monkeypatch)
    rows = archive["chats"][str(CHAT_ID)]["campaigns"]
    rows[0]["epilogue"] = ""
    rows[1]["epilogue"] = ""
    rows[1]["finale"] = ""

    text = render_party_history(dnd, CHAT_ID)

    assert "Письма победили, а партия ушла через окно" in text
    assert "Все выбрались на крышу" in text


def test_render_party_history_empty_archive(monkeypatch):
    monkeypatch.setattr(dnd_campaign, "_archive", {"version": 1, "chats": {}})
    monkeypatch.setattr(dnd_campaign, "_archive_loaded", True)

    text = render_party_history(dnd, CHAT_ID)

    assert "Прошедших партий" in text
    assert "пока нет" in text


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.chat = SimpleNamespace(id=CHAT_ID)
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


def test_history_middleware_intercepts_before_game_handlers(monkeypatch):
    _set_archive(monkeypatch)
    message = FakeMessage("днд партии")
    downstream = []

    async def handler(_event, _data):
        downstream.append(True)

    result = asyncio.run(DndPartyHistoryMiddleware()(handler, message, {}))

    assert result is None
    assert downstream == []
    assert len(message.answers) == 1
    assert "Санаторий снова потерял этаж" in message.answers[0][0]
