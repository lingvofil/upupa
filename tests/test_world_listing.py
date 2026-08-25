from types import SimpleNamespace

from handlers.world_listing import (
    format_current_states,
    is_states_list_command,
    states_with_current,
)


def test_states_listing_accepts_both_commands():
    assert is_states_list_command(SimpleNamespace(text="государства"))
    assert is_states_list_command(SimpleNamespace(text="Упупа, миры"))
    assert not is_states_list_command(SimpleNamespace(text="упупа мир"))


def test_states_listing_includes_current_state_and_uses_new_heading():
    current = SimpleNamespace(world_id=2, title="Current")
    others = (
        SimpleNamespace(world_id=3, title="Gamma"),
        SimpleNamespace(world_id=1, title="Alpha"),
    )

    states = states_with_current(current, others)
    text = format_current_states(states)

    assert [state.world_id for state in states] == [1, 2, 3]
    assert text == (
        "🌍 Текущие государства:\n"
        "№1 — Alpha\n"
        "№2 — Current\n"
        "№3 — Gamma"
    )
