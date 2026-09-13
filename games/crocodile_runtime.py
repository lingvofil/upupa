"""Explicit composition root for Crocodile runtime extensions."""

from __future__ import annotations


_configured = False


def configure_crocodile_runtime() -> None:
    """Install Crocodile runtime layers once in their dependency order."""
    global _configured
    if _configured:
        return

    from games import crocodile_party_state as party_state
    from games import crocodile_persistence as persistence
    from games.crocodile_admin_controls import configure_crocodile_admin_controls
    from games.crocodile_canvas_restore import configure_crocodile_canvas_restore
    from games.crocodile_controls import configure_crocodile_controls
    from games.crocodile_duo_optin import configure_crocodile_duo_opt_in
    from games.crocodile_modes import configure_crocodile_modes
    from games.crocodile_party_controls import configure_crocodile_party_controls
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
    persistence.configure_crocodile_persistence_dependencies(
        party_state.crocodile_persistence_dependencies()
    )
    configure_crocodile_party_controls()
    configure_crocodile_duo_opt_in()
    configure_crocodile_ui_enhancements()
    configure_crocodile_admin_controls()
    configure_crocodile_telephone_mentions()
    configure_crocodile_telephone_skip_permissions()
    configure_crocodile_telephone_roles()
    configure_crocodile_telephone_role_announcements()
    configure_crocodile_canvas_restore()
    _configured = True
