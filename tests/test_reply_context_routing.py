from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _reply(*, author_id: int, text=None, photo=None):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=author_id),
        text=text,
        audio=None,
        voice=None,
        video=None,
        photo=photo,
        animation=None,
        sticker=None,
        document=None,
    )


def test_quiz_words_in_reply_to_upupa_do_not_restart_quiz():
    from handlers import ai_profiles

    original_bot = ai_profiles.bot
    ai_profiles.bot = SimpleNamespace(id=999)
    try:
        message = SimpleNamespace(
            text="почему в викторине правильный третий вариант?",
            reply_to_message=_reply(author_id=999, text="Вопрос викторины"),
        )

        assert ai_profiles._should_start_quiz(message) is False
    finally:
        ai_profiles.bot = original_bot


def test_quiz_command_without_reply_still_starts_quiz():
    from handlers import ai_profiles

    message = SimpleNamespace(text="викторина", reply_to_message=None)

    assert ai_profiles._should_start_quiz(message) is True


def test_chotam_words_in_reply_to_text_analysis_continue_dialogue():
    from handlers import ai_vision

    original_bot = ai_vision.bot
    ai_vision.bot = SimpleNamespace(id=999)
    try:
        message = SimpleNamespace(
            text="а чотам справа?",
            caption=None,
            from_user=SimpleNamespace(id=123),
            reply_to_message=_reply(
                author_id=999,
                text="На фото машина у моря, справа виден дорожный знак.",
            ),
            audio=None,
            voice=None,
            video=None,
            photo=None,
            animation=None,
            sticker=None,
        )

        assert ai_vision._should_handle_whatisthere(message) is False
    finally:
        ai_vision.bot = original_bot


def test_chotam_reply_to_human_photo_still_runs_analysis():
    from handlers import ai_vision

    original_bot = ai_vision.bot
    ai_vision.bot = SimpleNamespace(id=999)
    try:
        message = SimpleNamespace(
            text="чотам",
            caption=None,
            from_user=SimpleNamespace(id=123),
            reply_to_message=_reply(author_id=321, photo=[object()]),
            audio=None,
            voice=None,
            video=None,
            photo=None,
            animation=None,
            sticker=None,
        )

        assert ai_vision._should_handle_whatisthere(message) is True
    finally:
        ai_vision.bot = original_bot
