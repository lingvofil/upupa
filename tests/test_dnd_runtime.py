from pathlib import Path

from tests import test_smoke_imports

del test_smoke_imports

from AI.dnd_completion import DndCompletionPolicy, DndParticipantCompletionMiddleware


ROOT = Path(__file__).resolve().parents[1]


def test_completion_policy_is_instance_dependency():
    policy = DndCompletionPolicy()
    middleware = DndParticipantCompletionMiddleware(policy=policy)

    assert middleware.policy is policy
    assert DndParticipantCompletionMiddleware().policy is not policy


def test_campaign_and_combat_use_policy_hooks_without_completion_class_mutation():
    campaign_source = (ROOT / "AI" / "dnd_campaign.py").read_text(encoding="utf-8")
    combat_source = (ROOT / "AI" / "dnd_combat.py").read_text(encoding="utf-8")
    artifact_stats_source = (ROOT / "AI" / "dnd_artifact_stats.py").read_text(encoding="utf-8")
    healing_choice_source = (ROOT / "AI" / "dnd_healing_choice.py").read_text(encoding="utf-8")
    two_heals_source = (ROOT / "AI" / "dnd_two_heals.py").read_text(encoding="utf-8")
    refinement_source = (ROOT / "AI" / "dnd_inventory_effect_refinement.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")
    dnd_source = (ROOT / "AI" / "dnd.py").read_text(encoding="utf-8")

    assert "completion_policy.after_participant_joined" in campaign_source
    assert "DndParticipantCompletionMiddleware._precollect_action_reply" not in campaign_source
    assert "old_precollect =" not in campaign_source

    assert "completion_policy.filter_expected_ids" in combat_source
    assert "DndParticipantCompletionMiddleware._expected_ids" not in combat_source
    assert "original_expected =" not in combat_source
    assert "campaign._ensure = ensure" not in combat_source
    assert "campaign._state = state" not in combat_source
    assert "campaign._restore_state = restore_state" not in artifact_stats_source
    assert "campaign._ensure = ensure" not in healing_choice_source
    assert "campaign._state = state" not in healing_choice_source
    assert "campaign._ensure = ensure" not in two_heals_source
    assert "campaign._state = state" not in two_heals_source
    assert "campaign._restore_state = restore_state" not in two_heals_source
    assert "campaign._restore_state = restore_state" not in refinement_source

    assert "DndCompletionPolicy()" in runtime_source
    assert "state_policy=campaign_state_policy" in runtime_source
    assert "install_dnd_event_journal(dnd, state_policy=campaign_state_policy)" in runtime_source
    assert "install_dnd_context_builder(dnd, campaign)" in runtime_source
    assert "install_dnd_session_canon_archive(dnd)" in runtime_source
    assert "install_dnd_adjudication(dnd, campaign)" in runtime_source
    assert "build_memory_context" in campaign_source
    assert "register_persist_hook" in dnd_source
    assert "build_bounded_text_prompt(self, message_text)" in dnd_source
    assert "history = self.conversation +" not in dnd_source
    assert "isolated_completion_middleware_class" not in runtime_source
    assert "middleware_class=" not in runtime_source
