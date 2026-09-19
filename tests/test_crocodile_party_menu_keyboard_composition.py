from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


def test_party_menu_keyboard_configurator_drives_stable_entrypoint():
    from games import crocodile_party_controls as party_controls

    original = party_controls.get_menu_keyboard_renderer()

    def renderer(chat_id):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"configured:{chat_id}",
                        callback_data="configured",
                    )
                ]
            ]
        )

    try:
        party_controls.configure_menu_keyboard_renderer(renderer)
        keyboard = party_controls.menu_keyboard("-42")
    finally:
        party_controls.configure_menu_keyboard_renderer(original)

    assert keyboard.inline_keyboard[0][0].text == "configured:-42"
    assert keyboard.inline_keyboard[0][0].callback_data == "configured"


def test_party_menu_keyboard_keeps_decorator_order():
    from games import crocodile_runtime as runtime

    seen = []

    def base(chat_id):
        seen.append(("base", str(chat_id)))
        return InlineKeyboardMarkup(inline_keyboard=[])

    def decorate_one(keyboard):
        seen.append(("one", len(keyboard.inline_keyboard)))
        return keyboard

    def decorate_two(keyboard):
        seen.append(("two", len(keyboard.inline_keyboard)))
        return keyboard

    renderer = runtime._compose_party_menu_keyboard(
        base,
        decorate_one,
        decorate_two,
    )

    renderer("-42")

    assert seen == [("base", "-42"), ("one", 0), ("two", 0)]
