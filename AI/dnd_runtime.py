"""Explicit DnD runtime composition for mechanics and completion policy hooks."""

from __future__ import annotations


def _critical_dnd_roll_note(result: int) -> str | None:
    if result == 20:
        return "КРИТИЧЕСКАЯ УДАЧА"
    if result == 1:
        return "КРИТИЧЕСКАЯ НЕУДАЧА"
    return None


def configure_dnd_runtime(dnd_router=None) -> None:
    """Compose DnD mechanics once with explicit completion policy dependencies."""
    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_completion as completion
    from AI.dnd_adjudication import install_dnd_adjudication
    from AI.dnd_any_bot_reply import configure_dnd_any_bot_replies
    from AI.dnd_artifact_guard import install_dnd_artifact_guard
    from AI.dnd_artifact_stats import install_dnd_artifact_stats
    from AI.dnd_campaign import configure_dnd_campaign
    from AI.dnd_campaign_state import DndCampaignStatePolicy, configure_dnd_campaign_state
    from AI.dnd_cinematic_combat import install_dnd_cinematic_combat
    from AI.dnd_combat import install_dnd_combat
    from AI.dnd_current_turn_priority import install_dnd_current_turn_priority
    from AI.dnd_context_builder import install_dnd_context_builder
    from AI.dnd_conditions import install_dnd_conditions
    from AI.dnd_death_legacy import install_dnd_death_legacy
    from AI.dnd_enemy_command import install_dnd_enemy_command
    from AI.dnd_enemy_stats import install_dnd_enemy_stats
    from AI.dnd_event_journal import install_dnd_event_journal
    from AI.dnd_epilogue_image import install_dnd_epilogue_image
    from AI.dnd_finalization_recovery import install_dnd_finalization_recovery
    from AI.dnd_generation_resilience import configure_dnd_generation_resilience
    from AI.dnd_group_action_resilience import install_dnd_group_action_resilience
    from AI.dnd_group_progress import install_dnd_group_progress
    from AI.dnd_growth import install_dnd_growth
    from AI.dnd_healing_choice import install_dnd_healing_choice
    from AI.dnd_image_quality import install_dnd_image_quality
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
    from AI.dnd_item_actions import install_dnd_item_actions
    from AI.dnd_lobby_controls import install_dnd_lobby_controls
    from AI.dnd_manual_healing import install_dnd_manual_healing
    from AI.dnd_metadata import DndMetadataPolicy, configure_dnd_metadata
    from AI.dnd_pacing import install_dnd_pacing
    from AI.dnd_party_history import install_dnd_party_history
    from AI.dnd_player_agency import configure_dnd_player_agency
    from AI.dnd_player_combat import install_dnd_player_combat
    from AI.dnd_player_positions import install_dnd_player_positions
    from AI.dnd_poll_agency import install_dnd_poll_agency
    from AI.dnd_plot_resilience import configure_dnd_plot_resilience
    from AI.dnd_profile_ownership import install_dnd_profile_ownership
    from AI.dnd_roll_ability_display import install_dnd_roll_ability_display
    from AI.dnd_roll_feedback import install_dnd_roll_feedback
    from AI.dnd_scaled_heals import install_dnd_scaled_heals
    from AI.dnd_scene_clocks import install_dnd_scene_clocks
    from AI.dnd_scene_tactics import install_dnd_scene_tactics
    from AI.dnd_session_canon import install_dnd_session_canon_archive
    from AI.dnd_spotlight import install_dnd_spotlight
    from AI.dnd_special_moves import install_dnd_special_moves
    from AI.dnd_state_commands import configure_dnd_state_commands
    from AI.dnd_target_mentions import configure_dnd_target_mentions
    from AI.dnd_turn_control import install_dnd_turn_control
    from AI.dnd_two_heals import install_dnd_two_heals
    from AI.dnd_two_heals_compat import install_dnd_two_heals_compat
    from AI.dnd_unknown_action_recovery import install_dnd_unknown_action_recovery
    from AI.dnd_world_memory import install_dnd_world_memory
    from AI.dnd_weakness_luck import install_dnd_weakness_luck

    router = dnd_router or dnd.dnd_router
    if getattr(router, "_upupa_dnd_runtime_configured", False):
        return

    # Presentation-level roll labels belong to the DnD composition root rather
    # than the generic application bootstrap mutating a private DnD function.
    dnd.configure_natural_roll_note(_critical_dnd_roll_note)

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
    install_dnd_party_history(router)

    # Install the bounded provider path before completion/campaign wrappers
    # capture generate_session_response, so every later DnD layer inherits it.
    install_dnd_adjudication(dnd, campaign)
    configure_dnd_generation_resilience(dnd)
    install_dnd_group_action_resilience(dnd)
    install_dnd_current_turn_priority(dnd, campaign_marker=campaign.MARKER)
    completion_policy = completion.DndCompletionPolicy()
    completion.configure_dnd_completion(router, policy=completion_policy)
    configure_dnd_campaign(dnd, router, completion_policy=completion_policy)
    configure_dnd_player_agency(dnd)
    configure_dnd_plot_resilience(campaign)
    configure_fun_inventory_rules(dnd)
    install_dnd_lobby_controls(router)
    install_dnd_profile_ownership(router)
    install_dnd_combat(
        router,
        completion_policy=completion_policy,
        state_policy=campaign_state_policy,
    )
    install_dnd_enemy_stats(dnd)
    install_dnd_unknown_action_recovery(dnd)
    install_dnd_player_combat(dnd, state_policy=campaign_state_policy)
    install_dnd_player_positions(dnd, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_roll_feedback(dnd)
    install_dnd_enemy_command(dnd)
    install_dnd_cinematic_combat(dnd)
    install_dnd_conditions(dnd, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_scene_clocks(dnd, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_scene_tactics(dnd, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_special_moves(dnd, router, state_policy=campaign_state_policy)
    install_dnd_weakness_luck(dnd, router, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_roll_ability_display(dnd)
    install_dnd_healing_choice(router, state_policy=campaign_state_policy)
    install_dnd_two_heals(router, state_policy=campaign_state_policy)
    install_dnd_two_heals_compat()
    install_dnd_scaled_heals(router)
    install_dnd_manual_healing(router)
    install_dnd_death_legacy(router)
    install_dnd_image_quality(dnd)
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
    install_dnd_artifact_stats(
        dnd,
        metadata_policy=metadata_policy,
        state_policy=campaign_state_policy,
    )
    install_dnd_inventory_effect_refinement(
        dnd,
        state_policy=campaign_state_policy,
    )
    configure_inventory_description_rules(dnd)
    install_dnd_inventory_descriptions(metadata_policy=metadata_policy)
    inventory_context_policy.renderer = render_inventory_description_context
    state_view_policy.inventory_items_renderer = render_inventory_description_lines
    install_dnd_artifact_guard(dnd, metadata_policy=metadata_policy)
    install_dnd_item_actions(dnd, router, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_growth(dnd, router, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_world_memory(dnd, state_policy=campaign_state_policy, metadata_policy=metadata_policy)
    install_dnd_session_canon_archive(dnd)
    # Must be installed after every archive/finish wrapper so one completion ID
    # guards the complete campaign archive and all post-finish effects.
    install_dnd_finalization_recovery(dnd)
    configure_dnd_any_bot_replies(router)

    # Target notifications must be inside spotlight. Spotlight may safely add a
    # missing TARGETS value; the mention layer must therefore see the normalized
    # action, not announce the model's pre-normalization target first.
    configure_dnd_target_mentions(dnd)
    install_dnd_spotlight(dnd, state_policy=campaign_state_policy)
    install_dnd_turn_control(dnd, router)
    install_dnd_poll_agency(dnd)
    # Pacing stays outermost so a blocked consecutive NPC attack becomes a
    # group INPUT before the spotlight layer classifies the next initiative.
    install_dnd_pacing(dnd)
    # Group-progress correction must sit outside campaign/metadata parse wrappers
    # so acknowledgment-only responses are replaced before they can mutate state.
    install_dnd_group_progress(
        dnd,
        state_policy=campaign_state_policy,
    )
    # Install the journal after all canonical state extensions so its restore
    # baseline sees the fully composed campaign representation.
    install_dnd_event_journal(dnd, state_policy=campaign_state_policy)
    install_dnd_context_builder(dnd, campaign)
    from AI.dnd_turn_contract import install_turn_contract_guard

    install_turn_contract_guard(dnd)

    completion.configure_dnd_campaign_compat(dnd)
    router._upupa_dnd_completion_policy = completion_policy
    router._upupa_dnd_campaign_state_policy = campaign_state_policy
    router._upupa_dnd_inventory_context_policy = inventory_context_policy
    router._upupa_dnd_metadata_policy = metadata_policy
    router._upupa_dnd_runtime_configured = True
