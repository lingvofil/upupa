from pathlib import Path
from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_artifact_guard import apply_artifact_guard
from AI.dnd_inventory_effects import apply_item_effect_metadata, format_inventory_entry
from AI.dnd_inventory_fun import apply_stackable_metadata
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


def test_metadata_policy_runs_downstream_processor_between_pre_and_post_processing():
    seen = []

    def downstream(_session, text):
        seen.append(("downstream", text))
        return "clean", ["base"]

    def preprocessor(text):
        seen.append(("pre", text))
        return f"{text}|pre"

    def processor(session, text, next_apply):
        seen.append(("processor-before", text))
        cleaned, notices = next_apply(session, f"{text}|stack")
        seen.append(("processor-after", cleaned))
        return cleaned, notices + ["stack"]

    def postprocessor(_session, original_text, cleaned, notices):
        seen.append(("post", original_text))
        return cleaned, notices + ["post"]

    policy = DndMetadataPolicy(downstream)
    policy.add_preprocessor(preprocessor)
    policy.add_downstream_processor(processor)
    policy.add_downstream_processor(processor)
    policy.add_postprocessor(postprocessor)

    assert policy.apply(SimpleNamespace(), "raw") == ("clean", ["base", "stack", "post"])
    assert len(policy.downstream_processors) == 1
    assert seen == [
        ("pre", "raw"),
        ("processor-before", "raw|pre"),
        ("downstream", "raw|pre|stack"),
        ("processor-after", "clean"),
        ("post", "raw"),
    ]


def test_metadata_policy_runs_postprocessor_after_downstream_with_original_text():
    seen = []

    def downstream(_session, text):
        seen.append(("downstream", text))
        return "clean", ["base"]

    def postprocessor(_session, original_text, cleaned, notices):
        seen.append(("postprocessor", original_text))
        return cleaned, notices + ["post"]

    policy = DndMetadataPolicy(downstream)
    policy.add_preprocessor(_expand_quantity_tags)
    policy.add_postprocessor(postprocessor)
    policy.add_postprocessor(postprocessor)
    tag = "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:2;KIND:item]"

    assert policy.apply(SimpleNamespace(), tag) == ("clean", ["base", "post"])
    assert len(policy.postprocessors) == 1
    assert "QTY:" not in seen[0][1]
    assert seen[1] == ("postprocessor", tag)


def test_metadata_policy_runs_around_processor_outside_pre_and_post_processing():
    seen = []

    def downstream(_session, text):
        seen.append(("downstream", text))
        return "clean", ["base"]

    def preprocessor(text):
        seen.append(("pre", text))
        return f"{text}|pre"

    def postprocessor(_session, original_text, cleaned, notices):
        seen.append(("post", original_text))
        return cleaned, notices + ["post"]

    def around_processor(session, text, next_apply):
        seen.append(("around-before", text))
        cleaned, notices = next_apply(session, f"{text}|guarded")
        seen.append(("around-after", cleaned))
        return cleaned, notices + ["around"]

    policy = DndMetadataPolicy(downstream)
    policy.add_preprocessor(preprocessor)
    policy.add_postprocessor(postprocessor)
    policy.add_around_processor(around_processor)
    policy.add_around_processor(around_processor)

    assert policy.apply(SimpleNamespace(), "raw") == ("clean", ["base", "post", "around"])
    assert len(policy.around_processors) == 1
    assert seen == [
        ("around-before", "raw"),
        ("pre", "raw|guarded"),
        ("downstream", "raw|guarded|pre"),
        ("post", "raw|guarded"),
        ("around-after", "clean"),
    ]


def test_artifact_guard_runs_before_metadata_core_and_adds_notice_after_it():
    session = SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Владелец"},
            "2": {"user_id": 2, "name": "Вор"},
        },
        inventories={
            "1": [{"name": "Ботинок Истины", "kind": "artifact"}],
            "2": [],
        },
    )
    seen = []

    def downstream(_session, text):
        seen.append(text)
        return text, ["base"]

    def around_guard(current_session, text, next_apply):
        return apply_artifact_guard(dnd_campaign, next_apply, current_session, text)

    policy = DndMetadataPolicy(downstream)
    policy.add_preprocessor(_expand_quantity_tags)
    policy.add_around_processor(around_guard)
    text = (
        "[ITEM:REMOVE;PLAYER:1;NAME:Ботинок Истины]"
        "[ITEM:ADD;PLAYER:2;NAME:Ботинок Истины;KIND:artifact]"
        "[ITEM:ADD;PLAYER:2;NAME:ложка;QTY:2;KIND:item]"
    )

    cleaned, notices = policy.apply(session, text)

    assert len(seen) == 1
    assert "Ботинок Истины" not in seen[0]
    assert "QTY:" not in seen[0]
    assert seen[0].count("[ITEM:ADD;PLAYER:2;NAME:ложка") == 2
    assert "Ботинок Истины" not in cleaned
    assert notices == [
        "base",
        "✨ Ботинок Истины остаётся у Владелец: чужие артефакты нельзя отбирать.",
    ]


def test_inventory_effect_postprocessor_preserves_qty_stack_then_effect_order():
    session = SimpleNamespace(
        mode="participants",
        participants={"1": {"user_id": 1, "name": "Ложечник"}},
    )

    def stack_processor(current_session, text, next_apply):
        return apply_stackable_metadata(
            dnd_campaign,
            next_apply,
            current_session,
            text,
        )

    def effect_postprocessor(current_session, original_text, cleaned, notices):
        return apply_item_effect_metadata(
            dnd_campaign,
            lambda _session, _text: (cleaned, notices),
            current_session,
            original_text,
        )

    policy = DndMetadataPolicy(dnd_campaign._apply_metadata)
    policy.add_downstream_processor(stack_processor)
    policy.add_preprocessor(_expand_quantity_tags)
    policy.add_postprocessor(effect_postprocessor)
    tag = (
        "[ITEM:ADD;PLAYER:1;NAME:ложка;QTY:5;FEW:ложки;MANY:ложек;KIND:item;"
        "BONUS:1;TRAIT:прожорливости]"
    )

    _cleaned, notices = policy.apply(session, tag)

    item = session.inventories["1"][0]
    assert item["quantity"] == 5
    assert item["bonus_per_unit"] == 1
    assert item["trait"] == "прожорливости"
    assert format_inventory_entry(item) == "5 ложек — +5 к прожорливости"
    assert any("5 ложек — +5 к прожорливости" in notice for notice in notices)


def test_metadata_configuration_keeps_one_stable_campaign_delegator():
    campaign = SimpleNamespace(_apply_metadata=lambda _session, text: (text, []))
    first = DndMetadataPolicy(campaign._apply_metadata)
    configured = configure_dnd_metadata(campaign, first)
    delegator = campaign._apply_metadata

    second = DndMetadataPolicy(lambda _session, text: ("other", []))
    assert configure_dnd_metadata(campaign, second) is configured
    assert campaign._apply_metadata is delegator
    assert campaign._apply_metadata(SimpleNamespace(), "hello") == ("hello", [])


def test_inventory_metadata_extensions_use_policy_instead_of_monkeypatching_apply():
    fun_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    reliability_source = (ROOT / "AI" / "dnd_inventory_reliability.py").read_text(encoding="utf-8")
    effects_source = (ROOT / "AI" / "dnd_inventory_effects.py").read_text(encoding="utf-8")
    guard_source = (ROOT / "AI" / "dnd_artifact_guard.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")
    policy_source = (ROOT / "AI" / "dnd_metadata.py").read_text(encoding="utf-8")
    fun_install = "    install_fun_inventory(\n"
    effects_install = "    install_dnd_inventory_effects(\n"

    assert "campaign._apply_metadata =" not in fun_source
    assert "campaign._apply_metadata =" not in reliability_source
    assert "campaign._apply_metadata =" not in effects_source
    assert "campaign._apply_metadata =" not in guard_source
    assert "metadata_policy.add_downstream_processor(process_metadata)" in fun_source
    assert "metadata_policy.add_preprocessor(_expand_quantity_tags)" in reliability_source
    assert "metadata_policy.add_postprocessor(postprocess_metadata)" in effects_source
    assert "metadata_policy.add_around_processor(around_metadata)" in guard_source
    assert "campaign._apply_metadata =" in policy_source
    assert "metadata_policy = configure_dnd_metadata(" in runtime_source
    assert "DndMetadataPolicy(campaign._apply_metadata)" in runtime_source
    assert fun_install in runtime_source
    assert "metadata_policy=metadata_policy" in runtime_source
    assert "install_dnd_inventory_reliability(dnd, metadata_policy=metadata_policy)" in runtime_source
    assert effects_install in runtime_source
    assert "state_policy=campaign_state_policy" in runtime_source
    assert "install_dnd_artifact_guard(dnd, metadata_policy=metadata_policy)" in runtime_source
    assert runtime_source.index("metadata_policy = configure_dnd_metadata(") < runtime_source.index(
        fun_install
    ) < runtime_source.index("install_dnd_inventory_reliability(dnd, metadata_policy=metadata_policy)")
    assert runtime_source.index("install_dnd_inventory_reliability(dnd, metadata_policy=metadata_policy)") < runtime_source.index(
        effects_install
    ) < runtime_source.index("install_dnd_artifact_guard(dnd, metadata_policy=metadata_policy)")
