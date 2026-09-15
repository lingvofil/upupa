from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _replied_message(*, author_id: int, author_name: str = "Упупа", is_bot: bool = True):
    return SimpleNamespace(
        text="Вот результат команды бота.",
        caption=None,
        poll=None,
        from_user=SimpleNamespace(
            id=author_id,
            is_bot=is_bot,
            full_name=author_name,
            first_name=author_name,
            username="upupa_bot" if is_bot else None,
        ),
        sender_chat=None,
        photo=None,
        video=None,
        animation=None,
        audio=None,
        voice=None,
        document=None,
        sticker=None,
    )


def _message(replied):
    return SimpleNamespace(
        reply_to_message=replied,
        bot=SimpleNamespace(id=999),
    )


def test_reply_context_marks_message_from_current_bot():
    from AI.dialog.generation import format_reply_context

    context = format_reply_context(_message(_replied_message(author_id=999)))

    assert "Автор сообщения: Упупа" in context
    assert "это сообщение написал ты сам" in context
    assert "текущий Telegram-бот Упупа" in context


def test_reply_context_does_not_mark_other_bot_as_self():
    from AI.dialog.generation import format_reply_context

    context = format_reply_context(
        _message(_replied_message(author_id=555, author_name="Другой бот"))
    )

    assert "Автор сообщения: Другой бот" in context
    assert "это сообщение написал ты сам" not in context


def test_reply_context_uses_id_not_display_name_for_self_detection():
    from AI.dialog.generation import format_reply_context

    context = format_reply_context(
        _message(_replied_message(author_id=123, author_name="Упупа", is_bot=False))
    )

    assert "Автор сообщения: Упупа" in context
    assert "это сообщение написал ты сам" not in context
