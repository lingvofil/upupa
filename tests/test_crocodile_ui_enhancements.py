import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def _callback(data: str, user_id: int = 1, name: str = "Первый"):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-42),
        message_id=777,
        edit_text=AsyncMock(),
        answer=AsyncMock(),
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, full_name=name),
        message=message,
        answer=AsyncMock(),
    )


def test_game_keyboard_renames_other_to_next(monkeypatch):
    from games import crocodile_ui_enhancements as ui

    monkeypatch.setattr(
        ui,
        "_original_get_game_keyboard",
        lambda chat_id: InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👁 Слово", callback_data=f"cr_w_{chat_id}")],
                [InlineKeyboardButton(text="🔄 Другое", callback_data=f"cr_n_{chat_id}")],
            ]
        ),
    )

    keyboard = ui.get_game_keyboard_with_clear_next(-42)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    next_button = next(button for button in buttons if button.callback_data == "cr_n_-42")
    assert next_button.text == "⏭ Следующее"


def test_main_menu_contains_ratings_before_gallery(monkeypatch):
    from games import crocodile_ui_enhancements as ui

    monkeypatch.setattr(
        ui,
        "_original_party_menu_keyboard",
        lambda chat_id: InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎨 Обычный", callback_data="cmenu_classic")],
                [InlineKeyboardButton(text="🖼 Галерея", callback_data="cmenu_gallery")],
            ]
        ),
    )

    keyboard = ui.menu_keyboard_with_ratings(-42)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["cmenu_classic", "cmenu_ratings", "cmenu_gallery"]


def test_ratings_submenu_contains_all_requested_views():
    from games import crocodile_ui_enhancements as ui

    keyboard = ui.ratings_menu_keyboard()
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }
    assert {
        "cmenu_rating_game",
        "cmenu_rating_artists",
        "cmenu_rating_likes",
        "cmenu_rating_slow",
        "cmenu_rating_longest",
        "cmenu_main",
    } <= callbacks


def test_unified_classic_start_reveals_word_in_callback_alert(monkeypatch):
    from games import crocodile
    from games import crocodile_party_controls
    from games import crocodile_ui_enhancements as ui

    crocodile.game_sessions.pop("-42", None)

    async def fake_start(chat_id, user_id, user_name):
        crocodile.game_sessions[str(chat_id)] = {
            "word": "барсук",
            "drawer_id": user_id,
            "drawer_name": user_name,
        }

    monkeypatch.setattr(crocodile_party_controls, "has_active_party", lambda chat_id: False)
    monkeypatch.setattr(crocodile, "start_new_game", fake_start)
    callback = _callback("cmenu_classic")

    try:
        asyncio.run(ui.handle_party_menu_callback_with_ratings(callback))
        callback.answer.assert_awaited_once_with("🎯 Твоё слово: БАРСУК", show_alert=True)
    finally:
        crocodile.game_sessions.pop("-42", None)


def test_direct_start_sends_word_privately(monkeypatch):
    from games import crocodile
    from games import crocodile_ui_enhancements as ui

    crocodile.game_sessions.pop("-42", None)

    async def fake_original(chat_id, user_id, user_name):
        crocodile.game_sessions[str(chat_id)] = {
            "word": "носорог",
            "drawer_id": user_id,
            "drawer_name": user_name,
        }

    send_word = AsyncMock(return_value=True)
    monkeypatch.setattr(ui, "_original_start_new_game", fake_original)
    monkeypatch.setattr(ui, "_send_word_privately", send_word)

    try:
        asyncio.run(ui.start_new_game_with_instant_word(-42, 123, "Первый"))
        send_word.assert_awaited_once_with(123, "носорог")
    finally:
        crocodile.game_sessions.pop("-42", None)


def test_final_like_button_gets_persistent_target_token(monkeypatch):
    from games import crocodile_ui_enhancements as ui

    monkeypatch.setattr(
        ui,
        "_original_get_end_game_keyboard",
        lambda likes=0: InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"❤️ {likes}", callback_data="btn_like")]
            ]
        ),
    )
    create_target = MagicMock(return_value="abc123")
    monkeypatch.setattr(ui.crocodile_ratings, "create_like_target", create_target)
    ctx = ui._final_like_context.set(
        {"chat_id": "-42", "artists": [(1, "Первый"), (2, "Второй")]}
    )
    try:
        keyboard = ui.get_end_game_keyboard_with_attribution(0)
    finally:
        ui._final_like_context.reset(ctx)

    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == "cr_like_abc123"
    create_target.assert_called_once_with("-42", [(1, "Первый"), (2, "Второй")])


def test_attributed_like_is_credited_only_after_unique_like(monkeypatch):
    from games import crocodile_ui_enhancements as ui

    callback = _callback("cr_like_abc123", user_id=9, name="Лайкер")
    monkeypatch.setattr(
        ui.crocodile_ratings,
        "get_like_target",
        lambda token: {"chat_id": "-42", "artists": [{"id": 1, "name": "Первый"}]},
    )
    credit = MagicMock(return_value=True)
    monkeypatch.setattr(ui.crocodile_ratings, "credit_like", credit)
    monkeypatch.setattr(ui.crocodile_likes, "handle_like_callback", AsyncMock())
    checks = iter([False, True])
    monkeypatch.setattr(ui, "_like_already_registered", lambda cb: next(checks))

    asyncio.run(ui._handle_attributed_like(callback, "abc123"))

    credit.assert_called_once_with("abc123")


def test_like_rating_credits_both_duo_artists(tmp_path, monkeypatch):
    from core.json_repository import JsonFileRepository
    from games import crocodile_ratings as ratings

    monkeypatch.setattr(
        ratings,
        "_repository",
        JsonFileRepository(tmp_path / "crocodile_like_ratings.json"),
    )
    monkeypatch.setattr(ratings.secrets, "token_urlsafe", lambda _n: "fixedtoken")

    token = ratings.create_like_target(-42, [(1, "Первый"), (2, "Второй")])
    assert token == "fixedtoken"
    assert ratings.credit_like(token)
    assert ratings.credit_like(token)

    text = ratings.format_like_leaderboard(-42)
    assert "Первый — <b>2</b> ❤️" in text
    assert "Второй — <b>2</b> ❤️" in text


def test_longest_single_draw_rating_uses_existing_max_time(monkeypatch):
    from games import crocodile_ratings as ratings

    monkeypatch.setattr(
        ratings.crocodile_scoring,
        "_load_artist_scores_sync",
        lambda: {
            "-42": {
                "1": {"name": "Медленный", "max_guess_seconds": 125},
                "2": {"name": "Быстрый", "max_guess_seconds": 25},
            }
        },
    )

    text = ratings.format_longest_single_draw_leaderboard(-42)
    assert text.index("Медленный") < text.index("Быстрый")
    assert "2 мин 05 сек" in text
