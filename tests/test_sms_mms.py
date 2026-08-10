import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _media_message(**overrides):
    base = {
        "photo": None,
        "video": None,
        "video_note": None,
        "animation": None,
        "audio": None,
        "voice": None,
        "document": None,
        "sticker": None,
        "poll": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_process_send_mms_forwards_video_note(monkeypatch):
    from features import sms_settings

    replies = []
    sent = []

    async def reply(text):
        replies.append(text)

    class FakeBot:
        async def send_video_note(self, chat_id, video_note):
            sent.append((chat_id, video_note))

    message = SimpleNamespace(
        text="ммс 1",
        caption=None,
        chat=SimpleNamespace(id=-1001, title="Source"),
        reply_to_message=_media_message(video_note=SimpleNamespace(file_id="video-note-file-id")),
        reply=reply,
    )

    monkeypatch.setattr(sms_settings, "sms_disabled_chats", set())

    asyncio.run(
        sms_settings.process_send_mms(
            message,
            [{"id": -1002, "title": "Target"}, {"id": -1001, "title": "Source"}],
            FakeBot(),
        )
    )

    assert sent == [("-1002", "video-note-file-id")]
    assert len(replies) == 1
