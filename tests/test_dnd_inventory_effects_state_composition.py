from pathlib import Path
from types import SimpleNamespace

from AI.dnd_campaign_state import DndCampaignStatePolicy
from AI.dnd_inventory_effects import (
    INVENTORY_EFFECT_MIGRATION_VERSION,
    _configure_session_schema_migration,
)


ROOT = Path(__file__).resolve().parents[1]


def _policy():
    def base_ensure(session):
        if not hasattr(session, "inventories"):
            session.inventories = {}

    def base_state(session):
        base_ensure(session)
        return {"inventories": session.inventories}

    def base_restore(session, data):
        base_ensure(session)
        if isinstance(data, dict) and "inventories" in data:
            session.inventories = data["inventories"]

    policy = DndCampaignStatePolicy(base_ensure, base_state, base_restore)
    _configure_session_schema_migration(policy)
    return policy


def test_legacy_session_restore_migrates_inventory_and_persists_version():
    policy = _policy()
    session = SimpleNamespace()
    data = {
        "inventories": {
            "1": [{"name": "пизд", "kind": "item", "quantity": 5}],
        }
    }

    policy.restore(session, data)

    item = session.inventories["1"][0]
    assert session.inventory_effects_version == INVENTORY_EFFECT_MIGRATION_VERSION
    assert item["bonus_per_unit"] == 1
    assert item["trait"] == "травматическому опыту"
    assert policy.state(session)["inventory_effects_version"] == INVENTORY_EFFECT_MIGRATION_VERSION


def test_current_session_restore_does_not_backfill_future_plain_item():
    policy = _policy()
    session = SimpleNamespace()
    data = {
        "inventory_effects_version": INVENTORY_EFFECT_MIGRATION_VERSION,
        "inventories": {
            "1": [{"name": "камень", "kind": "item"}],
        },
    }

    policy.restore(session, data)

    assert session.inventory_effects_version == INVENTORY_EFFECT_MIGRATION_VERSION
    assert session.inventories["1"][0] == {"name": "камень", "kind": "item"}


def test_inventory_effect_schema_hooks_are_idempotent():
    policy = _policy()
    _configure_session_schema_migration(policy)

    assert len(policy.ensure_hooks) == 1
    assert list(policy.state_fields) == ["inventory_effects_version"]
    assert len(policy.restore_hooks) == 1


def test_inventory_effects_use_state_policy_instead_of_campaign_state_monkeypatches():
    effects_source = (ROOT / "AI" / "dnd_inventory_effects.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "campaign._ensure =" not in effects_source
    assert "campaign._state =" not in effects_source
    assert "campaign._restore_state =" not in effects_source
    assert "state_policy.add_ensure_hook(_ensure_inventory_effects_version)" in effects_source
    assert 'state_policy.add_state_field("inventory_effects_version", _inventory_effects_version_state)' in effects_source
    assert "state_policy.add_restore_hook(_restore_inventory_effects_version)" in effects_source
    assert "state_policy=campaign_state_policy" in runtime_source
