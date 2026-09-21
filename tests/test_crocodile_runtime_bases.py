from pathlib import Path

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]


def test_default_handler_getters_are_stable_when_current_handlers_change():
    from games import crocodile
    from games import crocodile_controls
    from games import crocodile_modes
    from games import crocodile_party_controls as party_controls
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_modes as reverse_modes

    cases = (
        (
            crocodile.get_default_start_new_game_handler,
            crocodile.get_start_new_game_handler,
            crocodile.configure_start_new_game_handler,
        ),
        (
            crocodile.get_default_game_keyboard_renderer,
            crocodile.get_game_keyboard_renderer,
            crocodile.configure_game_keyboard_renderer,
        ),
        (
            crocodile.get_default_end_game_keyboard_renderer,
            crocodile.get_end_game_keyboard_renderer,
            crocodile.configure_end_game_keyboard_renderer,
        ),
        (
            crocodile.get_default_callback_handler,
            crocodile.get_callback_handler,
            crocodile.configure_callback_handler,
        ),
        (
            crocodile.get_default_check_answer_handler,
            crocodile.get_check_answer_handler,
            crocodile.configure_check_answer_handler,
        ),
        (
            crocodile.get_default_socket_room_authorizer,
            crocodile.get_socket_room_authorizer,
            crocodile.configure_socket_room_authorizer,
        ),
        (
            crocodile_controls.get_default_stop_lock_remaining_seconds_handler,
            crocodile_controls.get_stop_lock_remaining_seconds_handler,
            crocodile_controls.configure_stop_lock_remaining_seconds_handler,
        ),
        (
            crocodile_modes.get_default_start_telephone_handler,
            crocodile_modes.get_start_telephone_handler,
            crocodile_modes.configure_start_telephone_handler,
        ),
        (
            crocodile_modes.get_default_telephone_lobby_keyboard_renderer,
            crocodile_modes.get_telephone_lobby_keyboard_renderer,
            crocodile_modes.configure_telephone_lobby_keyboard_renderer,
        ),
        (
            crocodile_modes.get_default_send_telephone_step_handler,
            crocodile_modes.get_send_telephone_step_handler,
            crocodile_modes.configure_send_telephone_step_handler,
        ),
        (
            crocodile_modes.get_default_start_duel_handler,
            crocodile_modes.get_start_duel_handler,
            crocodile_modes.configure_start_duel_handler,
        ),
        (
            crocodile_modes.get_default_telephone_callback_handler,
            crocodile_modes.get_telephone_callback_handler,
            crocodile_modes.configure_telephone_callback_handler,
        ),
        (
            crocodile_modes.get_default_duel_callback_handler,
            crocodile_modes.get_duel_callback_handler,
            crocodile_modes.configure_duel_callback_handler,
        ),
        (
            party_controls.get_default_party_status_text_renderer,
            party_controls.get_party_status_text_renderer,
            party_controls.configure_party_status_text_renderer,
        ),
        (
            party_controls.get_default_skip_telephone_handler,
            party_controls.get_skip_telephone_handler,
            party_controls.configure_skip_telephone_handler,
        ),
        (
            party_controls.get_default_stop_active_party_handler,
            party_controls.get_stop_active_party_handler,
            party_controls.configure_stop_active_party_handler,
        ),
        (
            party_controls.get_default_menu_keyboard_renderer,
            party_controls.get_menu_keyboard_renderer,
            party_controls.configure_menu_keyboard_renderer,
        ),
        (
            party_controls.get_default_menu_callback_handler,
            party_controls.get_menu_callback_handler,
            party_controls.configure_menu_callback_handler,
        ),
        (
            reverse.get_default_callback_handler,
            reverse.get_callback_handler,
            reverse.configure_callback_handler,
        ),
        (
            reverse_modes.get_default_callback_handler,
            reverse_modes.get_callback_handler,
            reverse_modes.configure_callback_handler,
        ),
    )

    for get_default, get_current, configure in cases:
        original = get_current()
        default = get_default()

        def replacement(*_args, **_kwargs):
            return None

        try:
            configure(replacement)
            assert get_current() is replacement
            assert get_default() is default
        finally:
            configure(original)


def test_runtime_composes_from_stable_bases_not_current_handlers():
    source = (ROOT / "games" / "crocodile_runtime.py").read_text(encoding="utf-8")

    expected = (
        "crocodile.get_default_socket_room_authorizer()",
        "crocodile.get_default_start_new_game_handler()",
        "crocodile.get_default_game_keyboard_renderer()",
        "crocodile.get_default_end_game_keyboard_renderer()",
        "crocodile.get_default_callback_handler()",
        "crocodile.get_default_check_answer_handler()",
        "crocodile_controls.get_default_stop_lock_remaining_seconds_handler()",
        "crocodile_modes.get_default_start_telephone_handler()",
        "party_controls.get_default_party_status_text_renderer()",
        "party_controls.get_default_skip_telephone_handler()",
        "crocodile_modes.get_default_start_duel_handler()",
        "crocodile_modes.get_default_duel_callback_handler()",
        "party_controls.get_default_stop_active_party_handler()",
        "party_controls.get_default_menu_keyboard_renderer()",
        "party_controls.get_default_menu_callback_handler()",
        "reverse.get_default_callback_handler()",
        "reverse_modes.get_default_callback_handler()",
        "party_controls.handle_telephone_callback_resilient",
        "telephone_callback_handler = _compose_callback_handler(",
        "party_menu_renderer = _compose_party_menu_keyboard(",
    )
    for marker in expected:
        assert marker in source

    forbidden = (
        "crocodile.get_socket_room_authorizer()",
        "crocodile.get_start_new_game_handler()",
        "crocodile.get_game_keyboard_renderer()",
        "crocodile.get_end_game_keyboard_renderer()",
        "crocodile.get_callback_handler()",
        "crocodile.get_check_answer_handler()",
        "crocodile_controls.get_stop_lock_remaining_seconds_handler()",
        "crocodile_modes.get_start_telephone_handler()",
        "crocodile_modes.get_telephone_lobby_keyboard_renderer()",
        "party_controls.get_party_status_text_renderer()",
        "party_controls.get_skip_telephone_handler()",
        "crocodile_modes.get_send_telephone_step_handler()",
        "crocodile_modes.get_start_duel_handler()",
        "crocodile_modes.get_telephone_callback_handler()",
        "crocodile_modes.get_duel_callback_handler()",
        "party_controls.get_stop_active_party_handler()",
        "party_controls.get_menu_keyboard_renderer()",
        "party_controls.get_menu_callback_handler()",
        "reverse.get_callback_handler()",
        "reverse_modes.get_callback_handler()",
    )
    for marker in forbidden:
        assert marker not in source
