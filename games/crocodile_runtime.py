"""Explicit composition root for Crocodile runtime extensions."""

from __future__ import annotations

import logging


_configured = False


def _compose_session_record_enrichers(*enrichers):
    callbacks = tuple(callback for callback in enrichers if callback is not None)

    def enrich(chat_id: str, session: dict, record: dict) -> dict:
        for callback in callbacks:
            record = callback(chat_id, session, record)
        return record

    return enrich


def _compose_restored_session_enrichers(*enrichers):
    callbacks = tuple(callback for callback in enrichers if callback is not None)

    def enrich(record: dict, chat_id: str, session: dict) -> tuple[str, dict]:
        for callback in callbacks:
            chat_id, session = callback(record, chat_id, session)
        return chat_id, session

    return enrich


def _compose_extra_persistors(*persistors):
    callbacks = tuple(callback for callback in persistors if callback is not None)

    def persist(*, force: bool = False) -> bool:
        changed = False
        for callback in callbacks:
            try:
                if callback(force=force):
                    changed = True
            except Exception:
                logging.exception(
                    "[crocodile] extra persistence callback failed: %r", callback
                )
        return changed

    return persist


def _compose_extra_restorers(*restorers):
    callbacks = tuple(callback for callback in restorers if callback is not None)

    def restore() -> int:
        restored = 0
        for callback in callbacks:
            try:
                restored += int(callback() or 0)
            except Exception:
                logging.exception(
                    "[crocodile] extra restore callback failed: %r", callback
                )
        return restored

    return restore


def _compose_game_keyboard(base_keyboard, *decorators):
    callbacks = tuple(callback for callback in decorators if callback is not None)

    def render(chat_id):
        keyboard = base_keyboard(chat_id)
        for callback in callbacks:
            keyboard = callback(chat_id, keyboard)
        return keyboard

    return render


def _compose_end_game_keyboard(base_keyboard, *decorators):
    callbacks = tuple(callback for callback in decorators if callback is not None)

    def render(likes=0):
        keyboard = base_keyboard(likes)
        for callback in callbacks:
            keyboard = callback(likes, keyboard)
        return keyboard

    return render


def _compose_start_new_game(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def start(
            chat_id,
            user_id,
            user_full_name,
            _wrapper=wrapper,
            _next=next_handler,
        ):
            return await _wrapper(chat_id, user_id, user_full_name, _next)

        handler = start
    return handler


def _compose_start_duel(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def start(message, _wrapper=wrapper, _next=next_handler):
            return await _wrapper(message, _next)

        handler = start
    return handler


def _compose_start_telephone(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def start(message, _wrapper=wrapper, _next=next_handler):
            return await _wrapper(message, _next)

        handler = start
    return handler


def _compose_callback_handler(base_handler, *routers):
    handler = base_handler
    for router in routers:
        if router is None:
            continue
        next_handler = handler

        async def handle(callback, _router=router, _next=next_handler):
            return await _router(callback, _next)

        handler = handle
    return handler


def _compose_stop_lock_remaining_seconds(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        def remaining(
            session,
            user_id,
            *,
            now=None,
            _wrapper=wrapper,
            _next=next_handler,
        ):
            return _wrapper(session, user_id, _next, now=now)

        handler = remaining
    return handler


def _compose_party_stop_handler(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def stop(chat_id, user_id, _wrapper=wrapper, _next=next_handler):
            return await _wrapper(chat_id, user_id, _next)

        handler = stop
    return handler


def _compose_menu_keyboard_handler(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        def render(chat_id, _wrapper=wrapper, _next=next_handler):
            return _wrapper(chat_id, _next)

        handler = render
    return handler


def _compose_check_answer(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def check(message, _wrapper=wrapper, _next=next_handler):
            return await _wrapper(message, _next)

        handler = check
    return handler


def _compose_socket_room_authorizer(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def authorize(
            sid,
            data,
            *,
            bind_room=False,
            _wrapper=wrapper,
            _next=next_handler,
        ):
            return await _wrapper(
                sid,
                data,
                _next,
                bind_room=bind_room,
            )

        handler = authorize
    return handler


def _compose_socket_join_room(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def join_room(
            sid,
            data,
            _wrapper=wrapper,
            _next=next_handler,
        ):
            return await _wrapper(sid, data, _next)

        handler = join_room
    return handler


def _compose_socket_snapshot(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def snapshot(
            sid,
            data,
            callback=None,
            _wrapper=wrapper,
            _next=next_handler,
        ):
            return await _wrapper(
                sid,
                data,
                _next,
                callback=callback,
            )

        handler = snapshot
    return handler


def _compose_socket_final_frame(base_handler, *wrappers):
    handler = base_handler
    for wrapper in wrappers:
        if wrapper is None:
            continue
        next_handler = handler

        async def final_frame(
            sid,
            data,
            _wrapper=wrapper,
            _next=next_handler,
        ):
            return await _wrapper(sid, data, _next)

        handler = final_frame
    return handler


def _compose_party_menu_keyboard(base_menu, *decorators):
    callbacks = tuple(callback for callback in decorators if callback is not None)

    def render(chat_id):
        keyboard = base_menu(chat_id)
        for callback in callbacks:
            keyboard = callback(keyboard)
        return keyboard

    return render


def configure_crocodile_runtime() -> None:
    """Install Crocodile runtime layers once in their dependency order."""
    global _configured
    if _configured:
        return

    from games import crocodile
    from games import crocodile_controls
    from games import crocodile_modes
    from games import crocodile_duo_optin as duo_optin
    from games import crocodile_party_controls as party_controls
    from games import crocodile_party_state as party_state
    from games import crocodile_persistence as persistence
    from games import reverse_crocodile as reverse
    from games import reverse_crocodile_modes as reverse_modes
    from games import reverse_crocodile_persistence as reverse_persistence
    from games.crocodile_admin_controls import (
        configure_crocodile_admin_controls,
        handle_duel_callback_with_admin,
        handle_telephone_callback_with_admin,
        menu_keyboard_with_admin_emergency_stop,
        reverse_callback_with_admin,
        reverse_modes_callback_with_admin,
        stop_active_party_with_admin,
        stop_lock_remaining_seconds_with_admin,
    )
    from games.crocodile_canvas_restore import (
        configure_crocodile_canvas_restore,
        join_room_with_canvas_restore,
    )
    from games.crocodile_controls import (
        configure_crocodile_controls,
        decorate_game_keyboard_with_previous,
        handle_callback_with_controls,
        start_new_game_with_controls,
    )
    from games.crocodile_modes import (
        authorize_socket_room_with_modes,
        check_regular_answer_with_archive,
        configure_crocodile_modes,
        decorate_game_keyboard_with_legacy_duo,
        final_frame_with_modes,
        handle_regular_callback,
        snapshot_with_modes,
    )
    from games.crocodile_single_words import configure_crocodile_single_words
    from games.crocodile_telephone_mentions import configure_crocodile_telephone_mentions
    from games.crocodile_telephone_role_announcements import (
        configure_crocodile_telephone_role_announcements,
        start_telephone_with_role_announcement,
        telephone_callback_with_role_announcement,
    )
    from games.crocodile_telephone_roles import (
        configure_crocodile_telephone_roles,
        handle_telephone_callback_with_roles,
        start_telephone_with_roles,
    )
    from games.crocodile_telephone_skip_permissions import (
        configure_crocodile_telephone_skip_permissions,
        menu_callback_with_skip_permissions,
        telephone_callback_with_skip_permissions,
    )
    from games.crocodile_ui_enhancements import (
        check_answer_with_like_context,
        configure_crocodile_ui_enhancements,
        decorate_end_game_keyboard_with_attribution,
        decorate_game_keyboard_with_clear_next,
        decorate_party_menu_with_ratings,
        final_frame_with_like_context,
        handle_crocodile_callback_with_ui,
        handle_party_menu_callback_with_ratings,
        start_new_game_with_instant_word,
    )

    raw_authorize_socket_room = crocodile.get_default_socket_room_authorizer()
    raw_join_room = crocodile.join_room
    raw_snapshot = crocodile.snapshot
    raw_final_frame = crocodile.final_frame
    persistence.configure_crocodile_runtime()
    base_start_new_game = crocodile.get_default_start_new_game_handler()
    raw_game_keyboard = crocodile.get_default_game_keyboard_renderer()
    raw_callback_handler = crocodile.get_default_callback_handler()
    raw_check_answer = crocodile.get_default_check_answer_handler()
    configure_crocodile_controls(base_start_new_game=base_start_new_game)
    configure_crocodile_single_words()
    configure_crocodile_modes()
    crocodile.configure_socket_room_authorizer(
        _compose_socket_room_authorizer(
            raw_authorize_socket_room,
            persistence.authorize_socket_room_for_current_round,
            authorize_socket_room_with_modes,
        )
    )
    crocodile.sio.on(
        "join_room",
        handler=_compose_socket_join_room(
            raw_join_room,
            join_room_with_canvas_restore,
        ),
    )
    crocodile.sio.on(
        "snapshot",
        handler=_compose_socket_snapshot(raw_snapshot, snapshot_with_modes),
    )
    crocodile.sio.on(
        "final_frame",
        handler=_compose_socket_final_frame(
            raw_final_frame,
            final_frame_with_modes,
            final_frame_with_like_context,
        ),
    )
    pre_duo_game_keyboard = _compose_game_keyboard(
        raw_game_keyboard,
        decorate_game_keyboard_with_previous,
        decorate_game_keyboard_with_legacy_duo,
    )
    base_end_game_keyboard = crocodile.get_default_end_game_keyboard_renderer()
    party_dependencies = party_state.crocodile_persistence_dependencies()
    persistence.configure_crocodile_persistence_dependencies(
        persistence.CrocodilePersistenceDependencies(
            enrich_session_record=_compose_session_record_enrichers(
                duo_optin.enrich_session_record_with_duo_opt_in,
                party_dependencies.enrich_session_record,
            ),
            enrich_restored_session=_compose_restored_session_enrichers(
                duo_optin.enrich_restored_session_with_duo_opt_in,
                party_dependencies.enrich_restored_session,
            ),
            persist_extra_state=_compose_extra_persistors(
                party_dependencies.persist_extra_state,
                reverse_persistence.persist_reverse_crocodile_sessions,
            ),
            restore_extra_state=_compose_extra_restorers(
                party_dependencies.restore_extra_state,
                reverse_persistence.restore_reverse_crocodile_sessions,
            ),
        )
    )
    party_controls.configure_crocodile_party_controls()
    telephone_callback_handler = _compose_callback_handler(
        party_controls.handle_telephone_callback_resilient,
        telephone_callback_with_skip_permissions,
    )
    party_menu_renderer = _compose_party_menu_keyboard(
        party_controls.get_default_menu_keyboard_renderer(),
        duo_optin.decorate_party_menu_without_default_duo,
        decorate_party_menu_with_ratings,
    )
    party_controls.configure_menu_keyboard_renderer(party_menu_renderer)
    party_menu_callback_handler = _compose_callback_handler(
        party_controls.get_default_menu_callback_handler(),
        handle_party_menu_callback_with_ratings,
        menu_callback_with_skip_permissions,
    )
    party_controls.configure_menu_callback_handler(
        party_menu_callback_handler
    )
    crocodile.configure_game_keyboard_renderer(
        _compose_game_keyboard(
            raw_game_keyboard,
            decorate_game_keyboard_with_previous,
            decorate_game_keyboard_with_legacy_duo,
            duo_optin.decorate_game_keyboard_with_duo_opt_in,
            decorate_game_keyboard_with_clear_next,
        )
    )
    crocodile.configure_end_game_keyboard_renderer(
        _compose_end_game_keyboard(
            base_end_game_keyboard,
            decorate_end_game_keyboard_with_attribution,
        )
    )
    crocodile.configure_start_new_game_handler(
        _compose_start_new_game(
            base_start_new_game,
            start_new_game_with_controls,
            start_new_game_with_instant_word,
        )
    )
    crocodile.configure_callback_handler(
        _compose_callback_handler(
            raw_callback_handler,
            handle_callback_with_controls,
            handle_regular_callback,
            duo_optin.handle_duo_opt_in_callback,
            handle_crocodile_callback_with_ui,
        )
    )
    crocodile.configure_check_answer_handler(
        _compose_check_answer(
            raw_check_answer,
            check_regular_answer_with_archive,
            check_answer_with_like_context,
        )
    )
    duo_optin.configure_crocodile_duo_opt_in(
        base_game_keyboard=pre_duo_game_keyboard,
    )
    configure_crocodile_ui_enhancements()
    crocodile_controls.configure_stop_lock_remaining_seconds_handler(
        _compose_stop_lock_remaining_seconds(
            crocodile_controls.get_default_stop_lock_remaining_seconds_handler(),
            stop_lock_remaining_seconds_with_admin,
        )
    )
    telephone_callback_handler = _compose_callback_handler(
        telephone_callback_handler,
        handle_telephone_callback_with_admin,
    )
    crocodile_modes.configure_duel_callback_handler(
        _compose_callback_handler(
            crocodile_modes.get_default_duel_callback_handler(),
            handle_duel_callback_with_admin,
        )
    )
    party_controls.configure_stop_active_party_handler(
        _compose_party_stop_handler(
            party_controls.get_default_stop_active_party_handler(),
            stop_active_party_with_admin,
        )
    )
    party_menu_renderer = _compose_menu_keyboard_handler(
        party_menu_renderer,
        menu_keyboard_with_admin_emergency_stop,
    )
    party_controls.configure_menu_keyboard_renderer(party_menu_renderer)
    reverse.configure_callback_handler(
        _compose_callback_handler(
            reverse.get_default_callback_handler(),
            reverse_callback_with_admin,
        )
    )
    reverse_modes.configure_callback_handler(
        _compose_callback_handler(
            reverse_modes.get_default_callback_handler(),
            reverse_modes_callback_with_admin,
        )
    )
    configure_crocodile_admin_controls()
    configure_crocodile_telephone_mentions()
    configure_crocodile_telephone_skip_permissions()
    configure_crocodile_telephone_roles()
    telephone_callback_handler = _compose_callback_handler(
        telephone_callback_handler,
        handle_telephone_callback_with_roles,
    )
    configure_crocodile_telephone_role_announcements()
    telephone_callback_handler = _compose_callback_handler(
        telephone_callback_handler,
        telephone_callback_with_role_announcement,
    )
    crocodile_modes.configure_telephone_callback_handler(
        telephone_callback_handler
    )
    telephone_start_handler = _compose_start_telephone(
        crocodile_modes.get_default_start_telephone_handler(),
        party_controls.start_telephone_with_party_controls,
        start_telephone_with_roles,
        start_telephone_with_role_announcement,
    )
    crocodile_modes.configure_start_telephone_handler(
        telephone_start_handler
    )
    duel_start_handler = _compose_start_duel(
        crocodile_modes.get_default_start_duel_handler(),
        party_controls.start_duel_with_party_controls,
    )
    crocodile_modes.configure_start_duel_handler(
        duel_start_handler
    )
    configure_crocodile_canvas_restore()
    _configured = True
