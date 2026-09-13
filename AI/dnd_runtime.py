"""Explicit DnD runtime composition and isolation for legacy extension hooks."""

from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def isolated_completion_middleware_class():
    """Give legacy DnD installers a private middleware subclass to extend.

    Campaign/combat historically modify ``DndParticipantCompletionMiddleware`` at
    class level. Exposing a private subclass only while those installers run keeps
    that behavior local to the single configured runtime instance instead of
    mutating the exported middleware class for the whole process/test suite.
    """
    from AI import dnd_completion as completion

    base_class = completion.DndParticipantCompletionMiddleware
    runtime_class = type(
        "ConfiguredDndParticipantCompletionMiddleware",
        (base_class,),
        {"__module__": completion.__name__},
    )
    completion.DndParticipantCompletionMiddleware = runtime_class
    try:
        yield runtime_class
    finally:
        completion.DndParticipantCompletionMiddleware = base_class


def configure_dnd_runtime(dnd_router=None) -> None:
    """Compose DnD mechanics once while containing legacy class-level extensions."""
    from AI import dnd
    from AI import dnd_completion as completion
    from AI.dnd_campaign import configure_dnd_campaign
    from AI.dnd_combat import install_dnd_combat
    from AI.dnd_healing_choice import install_dnd_healing_choice
    from AI.dnd_inventory_fun import install_fun_inventory
    from AI.dnd_inventory_reliability import install_dnd_inventory_reliability
    from AI.dnd_lobby_controls import install_dnd_lobby_controls

    router = dnd_router or dnd.dnd_router
    if getattr(router, "_upupa_dnd_runtime_configured", False):
        return

    # Preserve the historical installation order exactly. The only difference is
    # that campaign/combat now extend a private runtime subclass instead of the
    # exported completion middleware class.
    install_fun_inventory()
    with isolated_completion_middleware_class() as runtime_middleware_class:
        completion.configure_dnd_completion(
            router,
            middleware_class=runtime_middleware_class,
        )
        configure_dnd_campaign(dnd, router)
        install_dnd_lobby_controls(router)
        install_dnd_combat(router)
        install_dnd_healing_choice(router)
        install_dnd_inventory_reliability(dnd)

    completion.configure_dnd_campaign_compat(dnd)
    router._upupa_dnd_runtime_configured = True
