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
                "players": {
                    "101": {"name": "Детектор"},
                    "202": {"name": "М&M"},
                },
                "campaigns": [
                    {
                        "completed_at": "2026-09-13T18:20:00+00:00",
                        "selected_plot": "Старый почтамт объявил войну адресатам",
                        "finale": "Письма победили, а партия ушла через окно.",
                        "epilogue": "Детектор сдох, захлебнувшись чернилами. М&M выбрался через окно с проклятым конвертом.",
                        "profiles": {"101": {"style": "алхимик"}, "202": {"style": "гонщик"}},
                        "inventories": {},
                        "scenes": ["Почтамт окончательно развалился."],
                    },
                    {
                        "completed_at": "2026-09-16T20:05:00+00:00",
                        "selected_plot": "Санаторий снова потерял этаж",
                        "finale": "Лифт признал поражение. [ACTION:END]",
                        "epilogue": "Ну шо, дегенераты, вы остались живы — чисто по случайности.",
                        "profiles": {"101": {"style": "алхимик"}, "202": {"style": "гонщик"}},
                        "inventories": {},
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


def test_render_party_history_is_readable_and_newest_first(monkeypatch):
    _set_archive(monkeypatch)

    text = render_party_history(dnd, CHAT_ID)

    assert "🎲 Партии DnD · 2" in text
    assert "16.09.2026 · Санаторий снова потерял этаж" in text
    assert "13.09.2026 · Старый почтамт объявил войну адресатам" in text
    assert text.index("16.09.2026") < text.index("13.09.2026")
    assert "👥 Детектор, М&M" in text
    assert "🏁 ✅ Все выжили." in text
    assert "☠️ Детектор — погиб: захлебнувшись чернилами" in text
    assert "✅ М&M — выжил: через окно с проклятым конвертом" in text
    assert "[ACTION:END]" not in text


def test_old_archive_resolves_names_from_player_history(monkeypatch):
    archive = _set_archive(monkeypatch)
    row = archive["chats"][str(CHAT_ID)]["campaigns"][0]
    assert "participants" not in row

    text = render_party_history(dnd, CHAT_ID)

    assert "👥 Детектор, М&M" in text
    assert "Детектор — погиб" in text
    assert "М&M — выжил" in text


def test_render_party_history_falls_back_to_saved_text_without_players(monkeypatch):
    archive = _set_archive(monkeypatch)
    row = archive["chats"][str(CHAT_ID)]["campaigns"][0]
    row["profiles"] = {}
    row["inventories"] = {}
    row["epilogue"] = ""

    text = render_party_history(dnd, CHAT_ID)

    assert "🏁 Письма победили, а партия ушла через окно." in text


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
