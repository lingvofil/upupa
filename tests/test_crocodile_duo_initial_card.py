from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_initial_card_offers_artist_controlled_duo_invite_without_session():
    from games import crocodile
    from games import crocodile_duo_optin as duo

    crocodile.game_sessions.pop("-42", None)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Открыть холст", url="https://example.com")]
        ]
    )

    keyboard = duo.decorate_game_keyboard_with_duo_opt_in(-42, keyboard)
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert "cr_duo_invite_-42" in callbacks
    assert "cr_duo_join_-42" not in callbacks
