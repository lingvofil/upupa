from pathlib import Path
from types import SimpleNamespace

from AI.dnd_inventory_reliability import _expand_quantity_tags
from AI.dnd_metadata import DndMetadataPolicy, configure_dnd_metadata


ROOT = Path(__file__).resolve().parents[1]


def test_metadata_policy_runs_quantity_expansion_before_downstream():
    seen = []

    def downstream(_session, text):
        seen.append(text)
        return "clean", []

    policy = DndMetadataPolicy(downstream)
    policy.add_preprocessor(_expand_quantity_tags)
    tag = "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:3;FEW:ложки;MANY:ложек;KIND:item]"

    assert policy.apply(SimpleNamespace(), tag) == ("clean", [])
    assert len(seen) == 1
    assert "QTY:" not in seen[0]
    assert seen[0].count("[ITEM:ADD;PLAYER:1;NAME:ложка") == 3


def test_metadata_policy_keeps_artifact_quantity_at_one_and_dedupes_preprocessor():
    seen = []
    policy = DndMetadataPolicy(lambda _session, text: seen.append(text) or (text, []))

    policy.add_preprocessor(_expand_quantity_tags)
    policy.add_preprocessor(_expand_quantity_tags)
    tag = "[ITEM:ADD;PLAYER:1;NAME:Ложка Судьбы;QTY:7;KIND:artifact]"
    policy.apply(SimpleNamespace(), tag)

    assert len(policy.preprocessors) == 1
    assert seen[0] == "[ITEM:ADD;PLAYER:1;NAME:Ложка Судьбы;KIND:artifact]"


def test_metadata_configuration_keeps_one_stable_campaign_delegator():
    campaign = SimpleNamespace(_apply_metadata=lambda _session, text: (text, []))
    first = DndMetadataPolicy(campaign._apply_metadata)
    configured = configure_dnd_metadata(campaign, first)
    delegator = campaign._apply_metadata

    second = DndMetadataPolicy(lambda _session, text: ("other", []))
    assert configure_dnd_metadata(campaign, second) is configured
    assert campaign._apply_metadata is delegator
    assert campaign._apply_metadata(SimpleNamespace(), "hello") == ("hello", [])


def test_inventory_reliability_uses_metadata_policy_instead_of_monkeypatching_apply():
    reliability_source = (ROOT / "AI" / "dnd_inventory_reliability.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")
    policy_source = (ROOT / "AI" / "dnd_metadata.py").read_text(encoding="utf-8")

    assert "campaign._apply_metadata =" not in reliability_source
    assert "metadata_policy.add_preprocessor(_expand_quantity_tags)" in reliability_source
    assert "campaign._apply_metadata =" in policy_source
    assert "metadata_policy = DndMetadataPolicy(campaign._apply_metadata)" in runtime_source
    assert "configure_dnd_metadata(campaign, metadata_policy)" in runtime_source
    assert "install_dnd_inventory_reliability(dnd, metadata_policy=metadata_policy)" in runtime_source
    assert runtime_source.index("install_fun_inventory()") < runtime_source.index(
        "metadata_policy = DndMetadataPolicy(campaign._apply_metadata)"
    ) < runtime_source.index("install_dnd_inventory_reliability(dnd, metadata_policy=metadata_policy)")
    assert runtime_source.index("install_dnd_inventory_reliability(dnd, metadata_policy=metadata_policy)") < runtime_source.index(
        "install_dnd_inventory_effects(dnd)"
    ) < runtime_source.index("install_dnd_artifact_guard(dnd)")
