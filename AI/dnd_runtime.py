"""Explicit DnD runtime composition for mechanics and completion policy hooks."""

from __future__ import annotations


def configure_dnd_runtime(dnd_router=None) -> None:
    """Compose DnD mechanics once with explicit completion policy dependencies."""
    from AI import dnd
    from AI import dnd_completion as completion
    from AI.dnd_campaign import configure_dnd_campaign
    from AI.dnd_combat import install_dnd_combat
    from AI.dnd_healing_choice import install_dnd_healing_choice
    from AI.dnd_inventory_effects import install_dnd_inventory_effects
    from AI.dnd_inventory_fun import install_fun_inventory
    from AI.dnd_inventory_reliability import install_dnd_inventory_reliability
    from AI.dnd_lobby_controls import install_dnd_lobby_controls

    router = dnd_router or dnd.dnd_router
    if getattr(router, "_upupa_dnd_runtime_configured", False):
        return

    # Preserve the historical installation order while making cross-layer
    # completion behavior explicit instead of mutating middleware classes.
    install_fun_inventory()
    completion_policy = completion.DndCompletionPolicy()
    completion.configure_dnd_completion(router, policy=completion_policy)
    configure_dnd_campaign(dnd, router, completion_policy=completion_policy)
    install_dnd_lobby_controls(router)
    install_dnd_combat(router, completion_policy=completion_policy)
    install_dnd_healing_choice(router)
    install_dnd_inventory_reliability(dnd)
    install_dnd_inventory_effects(dnd)

    completion.configure_dnd_campaign_compat(dnd)
    router._upupa_dnd_completion_policy = completion_policy
    router._upupa_dnd_runtime_configured = True
