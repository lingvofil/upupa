"""Explicit DnD runtime composition for mechanics and completion policy hooks."""

from __future__ import annotations


def configure_dnd_runtime(dnd_router=None) -> None:
    """Compose DnD mechanics once with explicit completion policy dependencies."""
    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_completion as completion
    from AI.dnd_any_bot_reply import configure_dnd_any_bot_replies
    from AI.dnd_artifact_guard import install_dnd_artifact_guard
    from AI.dnd_campaign import configure_dnd_campaign
    from AI.dnd_campaign_state import DndCampaignStatePolicy, configure_dnd_campaign_state
    from AI.dnd_cinematic_combat import install_dnd_cinematic_combat
    from AI.dnd_combat import install_dnd_combat
    from AI.dnd_death_legacy import install_dnd_death_legacy
    from AI.dnd_enemy_stats import install_dnd_enemy_stats
    from AI.dnd_epilogue_image import install_dnd_epilogue_image
    from AI.dnd_generation_resilience import configure_dnd_generation_resilience
    from AI.dnd_group_action_resilience import install_dnd_group_action_resilience
    from AI.dnd_healing_choice import install_dnd_healing_choice
    from AI.dnd_inventory_context import DndInventoryContextPolicy, configure_dnd_inventory_context
    from AI.dnd_inventory_descriptions import (
        _inventory_context as render_inventory_description_context,
        configure_inventory_description_rules,
        install_dnd_inventory_descriptions,
        render_inventory_lines as render_inventory_description_lines,
    )
    from AI.dnd_inventory_effect_refinement import install_dnd_inventory_effect_refinement
    from AI.dnd_inventory_effects import (
        _inventory_context as render_inventory_effect_context,
        configure_inventory_effect_rules,
        install_dnd_inventory_effects,
        render_inventory_lines as render_inventory_effect_lines,
    )
    from AI.dnd_inventory_fun import (
        _inventory_context as render_fun_inventory_context,
        build_fun_inventory_state_view_policy,
        configure_dnd_inventory_transfer,
        configure_fun_inventory_rules,
        install_fun_inventory,
    )
    from AI.dnd_inventory_reliability import install_dnd_inventory_reliability
    from AI.dnd_lobby_controls import install_dnd_lobby_controls
    from AI.dnd_metadata import DndMetadataPolicy, configure_dnd_metadata
    from AI.dnd_player_combat import install_dnd_player_combat
    from AI.dnd_plot_resilience import configure_dnd_plot_resilience
    from AI.dnd_scaled_heals import install_dnd_scaled_heals
    from AI.dnd_state_commands import configure_dnd_state_commands
    from AI.dnd_turn_priority import configure_dnd_turn_priority
    from AI.dnd_two_heals import install_dnd_two_heals
    from AI.dnd_two_heals_compat import install_dnd_two_heals_compat

    router = dnd_router or dnd.dnd_router
    if getattr(router, "_upupa_dnd_runtime_configured", False):
        return

    # Preserve the historical installation order while making cross-layer
    # completion behavior explicit instead of mutating middleware classes.
    campaign_state_policy = DndCampaignStatePolicy(
        campaign._ensure,
        campaign._state,
        campaign._restore_state,
    )
    configure_dnd_campaign_state(campaign, campaign_state_policy)
    metadata_policy = configure_dnd_metadata(
        campaign,
        DndMetadataPolicy(campaign._apply_metadata),
    )
    install_fun_inventory(
        state_policy=campaign_state_policy,
        metadata_policy=metadata_policy,
    )
    inventory_context_policy = DndInventoryContextPolicy(render_fun_inventory_context)
    configure_dnd_inventory_context(campaign, inventory_context_policy)
    configure_dnd_inventory_transfer(router)
    state_view_policy = build_fun_inventory_state_view_policy()
    configure_dnd_state_commands(router, view_policy=state_view_policy)

    # Install the bounded provider path first. Turn-priority is deliberately
    # installed before campaign wrapping: campaign will append its large state
    # context, then this inner wrapper re-appends the actual current actions at
    # the very end seen by Gemini/Groq.
    configure_dnd_generation_resilience(dnd)
    configure_dnd_turn_priority(dnd)
    install_dnd_group_action_resilience(dnd)
    completion_policy = completion.DndCompletionPolicy()
    completion.configure_dnd_completion(router, policy=completion_policy)
    configure_dnd_campaign(dnd, router, completion_policy=completion_policy)
    configure_dnd_plot_resilience(campaign)
    configure_fun_inventory_rules(dnd)
    install_dnd_lobby_controls(router)
    install_dnd_combat(router, completion_policy=completion_policy)
    install_dnd_enemy_stats(dnd)
    install_dnd_player_combat(dnd, state_policy=campaign_state_policy)
    install_dnd_cinematic_combat(dnd)
    install_dnd_healing_choice(router)
    install_dnd_two_heals(router)
    install_dnd_two_heals_compat()
    install_dnd_scaled_heals(router)
    install_dnd_death_legacy(router)
    install_dnd_epilogue_image(dnd)
    install_dnd_inventory_reliability(dnd, metadata_policy=metadata_policy)
    install_dnd_inventory_effects(
        dnd,
        metadata_policy=metadata_policy,
        state_policy=campaign_state_policy,
    )
    configure_inventory_effect_rules(dnd)
    inventory_context_policy.renderer = render_inventory_effect_context
    state_view_policy.inventory_items_renderer = render_inventory_effect_lines
    install_dnd_inventory_effect_refinement(dnd)
    configure_inventory_description_rules(dnd)
    install_dnd_inventory_descriptions(metadata_policy=metadata_policy)
    inventory_context_policy.renderer = render_inventory_description_context
    state_view_policy.inventory_items_renderer = render_inventory_description_lines
    install_dnd_artifact_guard(dnd, metadata_policy=metadata_policy)
    configure_dnd_any_bot_replies(router)

    completion.configure_dnd_campaign_compat(dnd)
    router._upupa_dnd_completion_policy = completion_policy
    router._upupa_dnd_campaign_state_policy = campaign_state_policy
    router._upupa_dnd_inventory_context_policy = inventory_context_policy
    router._upupa_dnd_metadata_policy = metadata_policy
    router._upupa_dnd_runtime_configured = True
