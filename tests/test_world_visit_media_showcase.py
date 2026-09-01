import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + моки)
from features.world.showcase import extract_showcase_content
import handlers.world_visit_media as media_handler


def _message(**overrides):
    values = {
        "text": None,
        "caption": None,
        "photo": None,
        "video": None,
        "animation": None,
        "sticker": None,
        "voice": None,
        "audio": None,
        "document": None,
        "video_note": None,
        "reply_to_message": None,
        "from_user": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_media_without_caption_is_valid_showcase():
    replied = SimpleNamespace(text="🛬 Государственный визит\n\nГости: государство №2 — Guest.", caption=None)
    message = _message(
        photo=[SimpleNamespace(file_id="photo")],
        reply_to_message=replied,
        from_user=SimpleNamespace(id=10),
    )

    showcase = extract_showcase_content(message)

    assert showcase is not None
    assert showcase.media_type == "photo"
    assert showcase.text == "[фото]"
    assert media_handler._is_visit_media_showcase_reply(message)


def test_media_summary_keeps_caption_sticker_emoji_and_document_name():
    photo = extract_showcase_content(_message(photo=[object()], caption="центральная площадь"))
    sticker = extract_showcase_content(_message(sticker=SimpleNamespace(emoji="😐")))
    document = extract_showcase_content(_message(document=SimpleNamespace(file_name="конституция.pdf")))

    assert photo is not None and photo.text == "[фото] центральная площадь"
    assert sticker is not None and sticker.text == "[стикер] 😐"
    assert document is not None and document.text == "[документ] конституция.pdf"


def test_plain_text_is_not_intercepted_by_media_router():
    replied = SimpleNamespace(text="🛬 Государственный визит\n\nГости: государство №2 — Guest.", caption=None)
    message = _message(
        text="главный гараж",
        reply_to_message=replied,
        from_user=SimpleNamespace(id=10),
    )

    assert not media_handler._is_visit_media_showcase_reply(message)


def test_media_showcase_is_copied_to_guest_and_journaled(monkeypatch):
    host = SimpleNamespace(world_id=1, chat_id=-1001, title="Host", enabled=True)
    guest = SimpleNamespace(world_id=2, chat_id=-1002, title="Guest", enabled=True)

    class Service:
        async def get_state(self, chat_id, title):
            return host

        async def get_state_by_world_id(self, world_id):
            return guest

    class FakeBot:
        def __init__(self):
            self.sent = []
            self.copied = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

        async def copy_message(self, **kwargs):
            self.copied.append(kwargs)

    replies = []

    async def reply(text):
        replies.append(text)

    message = _message(
        caption="центральная площадь",
        photo=[SimpleNamespace(file_id="photo")],
        reply_to_message=SimpleNamespace(
            text="🛬 Государственный визит\n\nГости: государство №2 — Guest.",
            caption=None,
        ),
        from_user=SimpleNamespace(id=10, full_name="Вася", username="vasya"),
        chat=SimpleNamespace(id=host.chat_id, title=host.title),
        message_id=777,
        reply=reply,
    )

    async def fake_require_world(_message):
        return Service()

    async def fake_get_open_visit(*args, **kwargs):
        return SimpleNamespace()

    recorded = []

    async def fake_record(*args, **kwargs):
        recorded.append((args, kwargs))
        return True

    fake_bot = FakeBot()
    monkeypatch.setattr(media_handler, "bot", fake_bot)
    monkeypatch.setattr(media_handler, "_require_world", fake_require_world)
    monkeypatch.setattr(media_handler, "get_open_visit", fake_get_open_visit)
    monkeypatch.setattr(media_handler, "visit_is_active", lambda visit: True)
    monkeypatch.setattr(media_handler, "record_interaction_event", fake_record)
    monkeypatch.setattr(media_handler, "_title", lambda _message: host.title)

    asyncio.run(media_handler.visit_media_showcase(message))

    assert fake_bot.copied == [
        {
            "chat_id": guest.chat_id,
            "from_chat_id": host.chat_id,
            "message_id": 777,
        }
    ]
    assert any("[фото] центральная площадь" in text for _chat_id, text in fake_bot.sent)
    assert replies == ["🎒 Показ засчитан и отправлен делегации."]
    assert recorded
    payload = recorded[0][1]["payload"]
    assert payload["text"] == "[фото] центральная площадь"
    assert payload["media_type"] == "photo"
    assert payload["telegram_message_id"] == 777
