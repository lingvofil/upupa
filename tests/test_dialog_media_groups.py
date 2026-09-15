import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _message(*, message_id: int, media_group_id: str | None, chat_id: int = 12345, is_bot: bool = False):
    return SimpleNamespace(
        message_id=message_id,
        media_group_id=media_group_id,
        text=None,
        caption=None,
        voice=None,
        chat=SimpleNamespace(
            id=chat_id,
            type="group",
            title="Test chat",
            username=None,
        ),
        from_user=SimpleNamespace(
            id=111,
            is_bot=is_bot,
            first_name="Human",
            full_name="Human",
        ),
    )


def test_media_group_followups_are_scoped_per_chat_and_ignore_bots():
    from features import dialog_pipeline

    dialog_pipeline._seen_media_groups.clear()

    assert dialog_pipeline._media_group_is_followup(
        _message(message_id=1, media_group_id="album-1")
    ) is False
    assert dialog_pipeline._media_group_is_followup(
        _message(message_id=2, media_group_id="album-1")
    ) is True
    assert dialog_pipeline._media_group_is_followup(
        _message(message_id=3, media_group_id="album-1", chat_id=999)
    ) is False
    assert dialog_pipeline._media_group_is_followup(
        _message(message_id=4, media_group_id="album-1", is_bot=True)
    ) is False


def test_accounting_helper_keeps_each_album_item_in_history_and_stats(monkeypatch):
    from features import dialog_pipeline

    calls = []

    async def fake_save(message):
        calls.append("save")

    def fake_record(message):
        calls.append("record")

    async def fake_track(message):
        calls.append("track")

    def fake_add(*args):
        calls.append("add")

    monkeypatch.setattr(
        dialog_pipeline.situational_summary,
        "_register_incoming_message",
        lambda message: True,
    )
    monkeypatch.setattr(dialog_pipeline, "save_user_message", fake_save)
    monkeypatch.setattr(dialog_pipeline, "record_participant_message", fake_record)
    monkeypatch.setattr(dialog_pipeline, "track_message_statistics", fake_track)
    monkeypatch.setattr(dialog_pipeline, "add_chat", fake_add)

    result = asyncio.run(
        dialog_pipeline._account_human_message(
            _message(message_id=2, media_group_id="album-1")
        )
    )

    assert result is True
    assert calls == ["save", "record", "track", "add"]


def test_dialog_pipeline_treats_album_as_one_conversational_event(monkeypatch):
    from features import dialog_pipeline

    dialog_pipeline._seen_media_groups.clear()
    reaction_messages = []
    accounting_messages = []
    general_messages = []

    async def fake_reactions(message):
        reaction_messages.append(message.message_id)
        return False

    async def fake_account(message):
        accounting_messages.append(message.message_id)
        return True

    async def fake_general(message):
        general_messages.append(message.message_id)

    monkeypatch.setattr(dialog_pipeline, "process_random_reactions_once", fake_reactions)
    monkeypatch.setattr(dialog_pipeline, "_account_human_message", fake_account)
    monkeypatch.setattr(dialog_pipeline, "process_general_dialog_message", fake_general)

    first = _message(message_id=10, media_group_id="album-2")
    second = _message(message_id=11, media_group_id="album-2")

    assert asyncio.run(dialog_pipeline.process_dialog_pipeline(first)) is False
    assert asyncio.run(dialog_pipeline.process_dialog_pipeline(second)) is False

    assert reaction_messages == [10]
    assert accounting_messages == [11]
    assert general_messages == [10]
