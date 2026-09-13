from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_unified_menu_advertises_classic_only_before_artist_opt_in():
    from games import crocodile_duo_optin as duo

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎨 Обычный / вдвоём",
                    callback_data="cmenu_classic",
                )
            ],
            [InlineKeyboardButton(text="⚔️ Дуэль", callback_data="cmenu_duel")],
        ]
    )

    keyboard = duo.decorate_party_menu_without_default_duo(keyboard)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    classic = next(button for button in buttons if button.callback_data == "cmenu_classic")
    assert classic.text == "🎨 Обычный"
    assert all("вдвоём" not in button.text.lower() for button in buttons)
    duel = next(button for button in buttons if button.callback_data == "cmenu_duel")
    assert duel.text == "⚔️ Дуэль"
