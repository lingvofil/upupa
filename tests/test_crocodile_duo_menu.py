from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_unified_menu_advertises_classic_only_before_artist_opt_in(monkeypatch):
    from games import crocodile_duo_optin as duo

    monkeypatch.setattr(
        duo,
        "_original_party_menu_keyboard",
        lambda chat_id: InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎨 Обычный / вдвоём",
                        callback_data="cmenu_classic",
                    )
                ],
                [InlineKeyboardButton(text="⚔️ Дуэль", callback_data="cmenu_duel")],
            ]
        ),
    )

    keyboard = duo.unified_menu_keyboard_without_default_duo(-42)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    classic = next(button for button in buttons if button.callback_data == "cmenu_classic")
    assert classic.text == "🎨 Обычный"
    assert all("вдвоём" not in button.text.lower() for button in buttons)
