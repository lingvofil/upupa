import ast
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assigned_attributes(source: str, module_name: str) -> list[str]:
    tree = ast.parse(source)
    assigned = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == module_name
            ):
                assigned.append(target.attr)
    return assigned


def test_game_keyboard_pipeline_keeps_previous_duo_and_clear_next():
    from games import crocodile
    from games import crocodile_controls as controls
    from games import crocodile_duo_optin as duo
    from games import crocodile_modes as modes
    from games import crocodile_runtime as runtime
    from games import crocodile_ui_enhancements as ui

    chat_id = -42
    crocodile.game_sessions[str(chat_id)] = {
        "drawer_id": 1,
        "drawer_name": "Первый",
    }

    def base_keyboard(cid: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎨 Холст", url="https://example.com")],
                [
                    InlineKeyboardButton(text="👁 Слово", callback_data=f"cr_w_{cid}"),
                    InlineKeyboardButton(text="⏭ Другое", callback_data=f"cr_n_{cid}"),
                    InlineKeyboardButton(text="🛑 Стоп", callback_data=f"cr_stop_{cid}"),
                ],
            ]
        )

    renderer = runtime._compose_game_keyboard(
        base_keyboard,
        controls.decorate_game_keyboard_with_previous,
        modes.decorate_game_keyboard_with_legacy_duo,
        duo.decorate_game_keyboard_with_duo_opt_in,
        ui.decorate_game_keyboard_with_clear_next,
    )

    try:
        keyboard = renderer(chat_id)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for button in buttons if button.callback_data]

        assert f"cr_p_{chat_id}" in callbacks
        assert f"cr_stop_{chat_id}" in callbacks
        assert f"cr_duo_invite_{chat_id}" in callbacks
        assert f"cr_duo_{chat_id}" not in callbacks
        next_button = next(
            button for button in buttons if button.callback_data == f"cr_n_{chat_id}"
        )
        assert next_button.text == "⏭ Следующее"
    finally:
        crocodile.game_sessions.pop(str(chat_id), None)


def test_modes_legacy_duo_decorator_preserves_existing_keyboard():
    from games import crocodile_modes as modes

    chat_id = -42
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Холст", url="https://example.com")],
            [InlineKeyboardButton(text="↩️ Предыдущее", callback_data=f"cr_p_{chat_id}")],
        ]
    )

    decorated = modes.decorate_game_keyboard_with_legacy_duo(chat_id, keyboard)
    callbacks = [
        button.callback_data
        for row in decorated.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert f"cr_p_{chat_id}" in callbacks
    assert f"cr_duo_{chat_id}" in callbacks


def test_game_keyboard_renderer_configurator_drives_stable_entrypoint():
    from games import crocodile

    original = crocodile.get_game_keyboard_renderer()

    def renderer(chat_id):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"configured:{chat_id}", callback_data="configured")]
            ]
        )

    try:
        crocodile.configure_game_keyboard_renderer(renderer)
        keyboard = crocodile.get_game_keyboard(-42)
        assert keyboard.inline_keyboard[0][0].text == "configured:-42"
        assert keyboard.inline_keyboard[0][0].callback_data == "configured"
    finally:
        crocodile.configure_game_keyboard_renderer(original)


def test_game_keyboard_entrypoint_is_composed_only_in_runtime():
    controls_source = _source("games/crocodile_controls.py")
    modes_source = _source("games/crocodile_modes.py")
    runtime_source = _source("games/crocodile_runtime.py")

    controls_assigned = _assigned_attributes(controls_source, "crocodile")
    modes_assigned = _assigned_attributes(modes_source, "crocodile")
    assert "get_game_keyboard" not in controls_assigned
    assert "get_game_keyboard" not in modes_assigned
    assert "_original_get_game_keyboard" not in controls_source
    assert "_original_get_game_keyboard" not in modes_source
    assert "get_game_keyboard_with_previous" not in controls_source
    assert "get_game_keyboard_with_duo" not in modes_source
    assert "decorate_game_keyboard_with_previous" in controls_source
    assert "decorate_game_keyboard_with_legacy_duo" in modes_source

    violations = []
    for path in sorted((ROOT / "games").glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        if "get_game_keyboard" in _assigned_attributes(source, "crocodile"):
            violations.append(relative)
    assert not violations, (
        "crocodile.get_game_keyboard нельзя заменять прямым присваиванием; "
        "используй configure_game_keyboard_renderer(): " + ", ".join(violations)
    )

    crocodile_source = _source("games/crocodile.py")
    assert "def get_game_keyboard_renderer(" in crocodile_source
    assert "def configure_game_keyboard_renderer(" in crocodile_source

    raw_capture = runtime_source.index(
        "raw_game_keyboard = crocodile.get_game_keyboard_renderer()"
    )
    controls_install = runtime_source.index(
        "configure_crocodile_controls(base_start_new_game=base_start_new_game)"
    )
    modes_install = runtime_source.index("configure_crocodile_modes()")
    pre_duo = runtime_source.index("pre_duo_game_keyboard = _compose_game_keyboard(")
    pre_previous = runtime_source.index(
        "decorate_game_keyboard_with_previous,",
        pre_duo,
    )
    pre_legacy = runtime_source.index(
        "decorate_game_keyboard_with_legacy_duo,",
        pre_previous,
    )
    final_entrypoint = "crocodile.configure_game_keyboard_renderer("
    assert runtime_source.count(final_entrypoint) == 1
    assert "crocodile.get_game_keyboard =" not in runtime_source
    final_wiring = runtime_source.index(final_entrypoint)
    final_previous = runtime_source.index(
        "decorate_game_keyboard_with_previous,",
        final_wiring,
    )
    final_legacy = runtime_source.index(
        "decorate_game_keyboard_with_legacy_duo,",
        final_previous,
    )
    final_duo = runtime_source.index(
        "duo_optin.decorate_game_keyboard_with_duo_opt_in,",
        final_legacy,
    )
    final_ui = runtime_source.index(
        "decorate_game_keyboard_with_clear_next,",
        final_duo,
    )
    duo_dependency = runtime_source.index(
        "base_game_keyboard=pre_duo_game_keyboard,",
        final_ui,
    )

    assert (
        raw_capture
        < controls_install
        < modes_install
        < pre_duo
        < pre_previous
        < pre_legacy
        < final_wiring
        < final_previous
        < final_legacy
        < final_duo
        < final_ui
        < duo_dependency
    )
