from types import SimpleNamespace

from AI.dnd_campaign_state import DndCampaignStatePolicy


def test_registered_state_fields_restore_without_downstream_knowledge():
    restored = []
    session = SimpleNamespace(core="old", extension={"value": 0})

    def downstream_ensure(current):
        if not hasattr(current, "core"):
            current.core = "default"

    def downstream_state(current):
        return {"core": current.core}

    def downstream_restore(current, data):
        current.core = data.get("core", "default")

    policy = DndCampaignStatePolicy(
        downstream_ensure,
        downstream_state,
        downstream_restore,
    )
    policy.add_state_field("extension", lambda current: current.extension)
    policy.add_restore_hook(
        lambda current, _data: restored.append(dict(current.extension))
    )

    policy.restore(
        session,
        {
            "core": "restored",
            "extension": {"value": 42},
        },
    )

    assert session.core == "restored"
    assert session.extension == {"value": 42}
    assert restored == [{"value": 42}]


def test_registered_state_fields_are_in_snapshot():
    session = SimpleNamespace(core="base", extension={"value": 7})

    policy = DndCampaignStatePolicy(
        lambda _session: None,
        lambda current: {"core": current.core},
        lambda _session, _data: None,
    )
    policy.add_state_field("extension", lambda current: current.extension)

    assert policy.state(session) == {
        "core": "base",
        "extension": {"value": 7},
    }
