"""Explicit composition root for Crocodile runtime extensions."""

from __future__ import annotations


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
            if callback(force=force):
                changed = True
        return changed

    return persist


def _compose_extra_restorers(*restorers):
    callbacks = tuple(callback for callback in restorers if callback is not None)

    def restore() -> int:
        restored = 0
        for callback in callbacks:
            restored += int(callback() or 0)
        return restored

    return restore


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

    from games import crocodile_duo_optin as duo_optin
    from games import crocodile_party_controls as party_controls
    from games import crocodile_party_state as party_state
    from games import crocodile_persistence as persistence
    from games import reverse_crocodile_persistence as reverse_persistence
    from games.crocodile_admin_controls import configure_crocodile_admin_controls
    from games.crocodile_canvas_restore import configure_crocodile_canvas_restore
    from games.crocodile_controls import configure_crocodile_controls
    from games.crocodile_modes import configure_crocodile_modes
    from games.crocodile_single_words import configure_crocodile_single_words
    from games.crocodile_telephone_mentions import configure_crocodile_telephone_mentions
    from games.crocodile_telephone_role_announcements import (
        configure_crocodile_telephone_role_announcements,
    )
    from games.crocodile_telephone_roles import configure_crocodile_telephone_roles
    from games.crocodile_telephone_skip_permissions import (
        configure_crocodile_telephone_skip_permissions,
    )
    from games.crocodile_ui_enhancements import configure_crocodile_ui_enhancements

    persistence.configure_crocodile_runtime()
    configure_crocodile_controls()
    configure_crocodile_single_words()
    configure_crocodile_modes()
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
    party_controls.menu_keyboard = _compose_party_menu_keyboard(
        party_controls.menu_keyboard,
        duo_optin.decorate_party_menu_without_default_duo,
    )
    duo_optin.configure_crocodile_duo_opt_in()
    configure_crocodile_ui_enhancements()
    configure_crocodile_admin_controls()
    configure_crocodile_telephone_mentions()
    configure_crocodile_telephone_skip_permissions()
    configure_crocodile_telephone_roles()
    configure_crocodile_telephone_role_announcements()
    configure_crocodile_canvas_restore()
    _configured = True
