"""Hero portrait on the «Герой» card: drawn once, uploaded once, redrawn on request."""
import asyncio
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401
from tests.test_dnd_menu import Callback, FakeBot, FakeDnd, button, session

from AI import dnd_hero_portrait as portraits
from AI import dnd_menu as menu


class PhotoBot(FakeBot):
    def __init__(self):
        super().__init__()
        self.photos = []
        self.captions = []
        self.media = []
        self.deleted = []
        self.uploads = 0

    async def send_photo(self, chat_id, photo, *, caption=None, **kwargs):
        self.next_id += 1
        if not isinstance(photo, str):
            self.uploads += 1
            file_id = f"file-{self.uploads}"
        else:
            file_id = photo
        row = SimpleNamespace(message_id=self.next_id, chat=SimpleNamespace(id=chat_id), text=caption, caption=caption,
                              photo=[SimpleNamespace(file_id="thumb"), SimpleNamespace(file_id=file_id)], **kwargs)
        self.photos.append(row)
        self.sent.append(row)
        return row

    async def edit_message_caption(self, *, caption, **kwargs):
        self.captions.append(SimpleNamespace(caption=caption, text=caption, **kwargs))
        self.edited.append(self.captions[-1])
        return True

    async def edit_message_media(self, *, media, **kwargs):
        self.uploads += 1
        row = SimpleNamespace(photo=[SimpleNamespace(file_id=f"file-{self.uploads}")], text=media.caption, **kwargs)
        self.media.append(row)
        self.edited.append(row)
        return row

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


def hero_session(**extra):
    current = session(**extra)
    current.character_profiles = {"1": {"gender": "женский", "style": "рыжая контрабандистка в лётных очках",
                                        "strength": "чует опасные сделки", "weakness": "лезет проверять запретное",
                                        "special": "блеф с каменным лицом"}}
    return current


@pytest.fixture(autouse=True)
def clean_portrait_cache(monkeypatch):
    portraits._pending.clear()
    portraits._inflight.clear()
    monkeypatch.setattr("AI.dnd_settings.settings_for", lambda session=None: {"images": "important"})
    yield
    portraits._pending.clear()
    portraits._inflight.clear()


def setup(current=None, *, images=b"PNG"):
    current = current or hero_session()
    dnd, bot = FakeDnd(current), PhotoBot()
    service = menu.DndMenuService(dnd)
    service.portraits_enabled = True
    calls = []

    async def fake_generate(prompt, **_kwargs):
        calls.append(prompt)
        await asyncio.sleep(0)
        return images, "fake"

    return current, dnd, bot, service, calls, fake_generate


async def settle():
    for _ in range(5):
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.gather(*pending)


def test_first_hero_open_draws_once_then_card_becomes_photo_and_reuses_file_id(monkeypatch):
    async def run():
        current, _dnd, bot, service, calls, fake = setup()
        monkeypatch.setattr("AI.dnd_image_quality.generate_dnd_image_bytes", fake)
        await service.show(bot, current, 1, "hero")
        assert "Портрет рисуется" in bot.sent[-1].text
        await settle()
        # The finished portrait replaced the text card with a photo card.
        assert len(calls) == 1 and bot.uploads == 1
        card = current.menu_ui_state["cards"]["1"]
        assert bot.photos[-1].message_id == card and current.menu_ui_state["kinds"]["1"] == "photo"
        assert current.hero_portraits["1"]["file_id"] == "file-1"
        assert "Маша" in bot.photos[-1].caption and "Портрет рисуется" not in bot.photos[-1].caption
        # Refreshing the hero page edits the caption only.
        await service.show(bot, current, 1, "hero")
        assert bot.captions and current.menu_ui_state["cards"]["1"] == card
        # Leaving the hero page: a photo cannot become text, so a new text card replaces it.
        await service.show(bot, current, 1, "items")
        assert current.menu_ui_state["kinds"]["1"] == "text" and card in bot.deleted
        # Coming back reuses the uploaded file, without drawing or uploading again.
        await service.show(bot, current, 1, "hero")
        await settle()
        assert len(calls) == 1 and bot.uploads == 1 and bot.photos[-1].photo[-1].file_id == "file-1"
    asyncio.run(run())


def test_portrait_button_redraws_with_cooldown_and_swaps_media_in_place(monkeypatch):
    async def run():
        current, _dnd, bot, service, calls, fake = setup()
        monkeypatch.setattr("AI.dnd_image_quality.generate_dnd_image_bytes", fake)
        await service.show(bot, current, 1, "hero")
        await settle()
        card = current.menu_ui_state["cards"]["1"]
        data = button(bot.photos[-1].reply_markup, "🎨 Новый портрет")
        callback = Callback(bot, current, data, message_id=card)
        await service.callback(callback)
        assert "через" in str(callback.answers[-1])  # redraw cooldown
        assert len(calls) == 1
        current.hero_portraits["1"]["requested_at"] -= portraits.REGEN_COOLDOWN_SECONDS + 1
        data = button(bot.edited[-1].reply_markup if bot.edited else bot.photos[-1].reply_markup, "🎨 Новый портрет")
        await service.callback(Callback(bot, current, data, message_id=card))
        await settle()
        assert len(calls) == 2
        assert bot.media and current.menu_ui_state["cards"]["1"] == card
        assert current.hero_portraits["1"]["file_id"] == "file-2" and current.hero_portraits["1"]["version"] == 2
    asyncio.run(run())


def test_new_hero_profile_never_shows_the_previous_heros_portrait(monkeypatch):
    async def run():
        current, _dnd, bot, service, calls, fake = setup()
        monkeypatch.setattr("AI.dnd_image_quality.generate_dnd_image_bytes", fake)
        await service.show(bot, current, 1, "hero")
        await settle()
        current.character_profiles["1"]["style"] = "лысый монах с медным чайником"
        assert portraits.photo_for(current, 1) is None
        await service.show(bot, current, 1, "hero")
        await settle()
        assert len(calls) == 2 and "медным чайником" in calls[-1]
    asyncio.run(run())


def test_images_off_draws_only_on_request_and_failure_does_not_loop(monkeypatch):
    async def run():
        current, _dnd, bot, service, calls, _fake = setup()
        monkeypatch.setattr("AI.dnd_settings.settings_for", lambda session=None: {"images": "off"})

        async def failing(prompt, **_kwargs):
            calls.append(prompt)
            return None, None

        monkeypatch.setattr("AI.dnd_image_quality.generate_dnd_image_bytes", failing)
        await service.show(bot, current, 1, "hero")
        await settle()
        assert calls == []
        card = current.menu_ui_state["cards"]["1"]
        await service.callback(Callback(bot, current, button(bot.sent[-1].reply_markup, "🎨 Нарисовать портрет"), message_id=card))
        await settle()
        assert len(calls) == 1 and current.menu_ui_state["kinds"]["1"] == "text"
        monkeypatch.setattr("AI.dnd_settings.settings_for", lambda session=None: {"images": "important"})
        await service.show(bot, current, 1, "hero")
        await settle()
        assert len(calls) == 1  # a failed automatic attempt is not retried on every open
    asyncio.run(run())


def test_portrait_survives_into_next_campaign_through_archive_when_hero_is_the_same(monkeypatch):
    current = hero_session()
    current.hero_portraits = {"1": {"file_id": "file-9", "sig": portraits.profile_signature(current.character_profiles["1"]),
                                    "version": 3, "created_at": 1.0}}
    players = {"1": {"profile": dict(current.character_profiles["1"])}}
    portraits.archive_portraits(current, players)
    assert players["1"]["portrait"]["file_id"] == "file-9"

    nxt = hero_session()
    nxt.hero_portraits = {}
    monkeypatch.setattr("AI.dnd_campaign._player_history", lambda chat_id, user_id: players.get(str(user_id)))
    assert portraits.photo_for(nxt, 1) == "file-9"
    nxt.character_profiles["1"]["style"] = "другой герой"
    nxt.hero_portraits = {}
    assert portraits.photo_for(nxt, 1) is None


def test_prompt_is_appearance_only_without_names_and_caption_fits_telegram():
    current = hero_session()
    prompt = portraits.build_portrait_prompt(current, 1)
    assert "рыжая контрабандистка" in prompt and "a woman" in prompt
    assert "Маша" not in prompt and "NO text" in prompt
    assert len(portraits.caption("x" * 5000)) == portraits.CAPTION_LIMIT
