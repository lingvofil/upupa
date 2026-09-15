from pathlib import Path
from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_campaign_state import DndCampaignStatePolicy, configure_dnd_campaign_state
from AI.dnd_inventory_fun import _ensure_artifact_awards


ROOT = Path(__file__).resolve().parents[1]


def _configure_artifact_awards(policy):
    policy.add_ensure_hook(_ensure_artifact_awards)
    policy.add_state_field("artifact_awards", _ensure_artifact_awards)
    return policy


def test_campaign_state_policy_persists_and_restores_artifact_awards():
    original_ensure = dnd_campaign._ensure
    original_state = dnd_campaign._state
    original_restore = dnd_campaign._restore_state
    previous_policy = getattr(dnd_campaign, "_upupa_dnd_campaign_state_policy", None)
    try:
        if hasattr(dnd_campaign, "_upupa_dnd_campaign_state_policy"):
            delattr(dnd_campaign, "_upupa_dnd_campaign_state_policy")
        policy = _configure_artifact_awards(DndCampaignStatePolicy(original_ensure, original_state))
        assert configure_dnd_campaign_state(dnd_campaign, policy) is policy

        session = SimpleNamespace(artifact_awards={"1": ["Ботинок Истины"]})
        state = dnd_campaign._state(session)
        assert state["artifact_awards"] == {"1": ["Ботинок Истины"]}

        restored = SimpleNamespace()
        original_restore(restored, state)
        assert restored.artifact_awards == {"1": ["Ботинок Истины"]}
    finally:
        dnd_campaign._ensure = original_ensure
        dnd_campaign._state = original_state
        if previous_policy is None:
            if hasattr(dnd_campaign, "_upupa_dnd_campaign_state_policy"):
                delattr(dnd_campaign, "_upupa_dnd_campaign_state_policy")
        else:
            dnd_campaign._upupa_dnd_campaign_state_policy = previous_policy


def test_artifact_awards_state_hook_normalizes_invalid_values():
    def base_ensure(session):
        if not hasattr(session, "core"):
            session.core = "ok"

    def base_state(session):
        base_ensure(session)
        return {"core": session.core}

    policy = _configure_artifact_awards(DndCampaignStatePolicy(base_ensure, base_state))
    session = SimpleNamespace(artifact_awards={"1": "broken"})

    policy.ensure(session)
    state = policy.state(session)

    assert session.artifact_awards == {"1": []}
    assert state == {"core": "ok", "artifact_awards": {"1": []}}


def test_campaign_state_configuration_is_idempotent():
    campaign = SimpleNamespace(
        _ensure=lambda _session: None,
        _state=lambda _session: {"base": True},
    )
    first = DndCampaignStatePolicy(campaign._ensure, campaign._state)
    configured = configure_dnd_campaign_state(campaign, first)
    ensure_delegator = campaign._ensure
    state_delegator = campaign._state

    second = DndCampaignStatePolicy(lambda _session: None, lambda _session: {"other": True})
    assert configure_dnd_campaign_state(campaign, second) is configured
    assert campaign._ensure is ensure_delegator
    assert campaign._state is state_delegator


def test_fun_inventory_uses_state_policy_instead_of_state_monkeypatches():
    fun_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    policy_source = (ROOT / "AI" / "dnd_campaign_state.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "campaign._ensure =" not in fun_source
    assert "campaign._state =" not in fun_source
    assert 'state_policy.add_ensure_hook(_ensure_artifact_awards)' in fun_source
    assert 'state_policy.add_state_field("artifact_awards", _ensure_artifact_awards)' in fun_source
    assert "campaign._ensure = policy.ensure" in policy_source
    assert "campaign._state = policy.state" in policy_source
    assert "campaign_state_policy = DndCampaignStatePolicy(campaign._ensure, campaign._state)" in runtime_source
    assert "configure_dnd_campaign_state(campaign, campaign_state_policy)" in runtime_source
    assert "install_fun_inventory(state_policy=campaign_state_policy)" in runtime_source
    assert runtime_source.index("configure_dnd_campaign_state(campaign, campaign_state_policy)") < runtime_source.index(
        "install_fun_inventory(state_policy=campaign_state_policy)"
    )
