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
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "completion_policy.after_participant_joined" in campaign_source
    assert "DndParticipantCompletionMiddleware._precollect_action_reply" not in campaign_source
    assert "old_precollect =" not in campaign_source

    assert "completion_policy.filter_expected_ids" in combat_source
    assert "DndParticipantCompletionMiddleware._expected_ids" not in combat_source
    assert "original_expected =" not in combat_source

    assert "DndCompletionPolicy()" in runtime_source
    assert "isolated_completion_middleware_class" not in runtime_source
    assert "middleware_class=" not in runtime_source
