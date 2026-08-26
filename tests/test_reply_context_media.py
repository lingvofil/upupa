from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def test_reply_context_keeps_media_caption_and_type():
    from AI.dialog.generation import format_reply_context

    replied = SimpleNamespace(
        text=None,
        caption="Результат анализа прикреплённого изображения",
        poll=None,
        from_user=SimpleNamespace(
            full_name="Упупа",
            first_name="Упупа",
            username="upupa_bot",
        ),
        sender_chat=None,
        photo=[object()],
        video=None,
        animation=None,
        audio=None,
        voice=None,
        document=None,
        sticker=None,
    )
    message = SimpleNamespace(reply_to_message=replied)

    context = format_reply_context(message)

    assert "Результат анализа прикреплённого изображения" in context
    assert "В сообщении также было: фото." in context
