from pathlib import Path
from types import SimpleNamespace

from tests import test_smoke_imports

del test_smoke_imports

from AI import dnd_state_commands as state_commands
from AI.dnd_inventory_fun import build_fun_inventory_state_view_policy
from AI.dnd_state_commands import DndStateCommandMiddleware, configure_dnd_state_commands


ROOT = Path(__file__).resolve().parents[1]


def _active_session():
    return SimpleNamespace(
        chat_id=-100777,
        participants={"7": {"user_id": 7, "name": "Семён"}},
        character_profiles={"7": {"style": "курьер", "strength": "наглость"}},
        reputations={"7": []},
        inventories={
            "7": [
                {"name": "штраф", "few": "штрафа", "many": "штрафов", "kind": "item", "quantity": 2},
                {"name": "Корона Подъезда", "kind": "artifact"},
            ]
        },
    )


def test_fun_inventory_view_policy_preserves_stack_formatting_and_transfer_help(monkeypatch):
    session = _active_session()
    campaign = SimpleNamespace(
        _ensure=lambda _session: None,
        _player_history=lambda _chat_id, _user_id: None,
    )
    monkeypatch.setattr(state_commands, "_campaign_module", lambda _dnd: campaign)
    dnd = SimpleNamespace(dnd_sessions={session.chat_id: session})
    policy = build_fun_inventory_state_view_policy()

    hero = state_commands.render_hero(
        dnd,
        session.chat_id,
        7,
        "Семён",
        view_policy=policy,
    )
    inventory = state_commands.render_inventory(
        dnd,
        session.chat_id,
        7,
        view_policy=policy,
    )

    assert "• 2 штрафа" in hero
    assert "✨ Корона Подъезда" in hero
    assert "• 2 штрафа" in inventory
    assert "↪️ Передача:" in inventory


def test_default_state_view_stays_independent_from_fun_inventory(monkeypatch):
    session = _active_session()
    campaign = SimpleNamespace(
        _ensure=lambda _session: None,
        _player_history=lambda _chat_id, _user_id: None,
    )
    monkeypatch.setattr(state_commands, "_campaign_module", lambda _dnd: campaign)
    dnd = SimpleNamespace(dnd_sessions={session.chat_id: session})

    inventory = state_commands.render_inventory(dnd, session.chat_id, 7)

    assert "• штраф" in inventory
    assert "• 2 штрафа" not in inventory
    assert "↪️ Передача:" not in inventory


def test_state_command_registration_keeps_explicit_view_policy():
    registered = []
    router = SimpleNamespace(
        message=SimpleNamespace(outer_middleware=lambda middleware: registered.append(middleware))
    )
    policy = build_fun_inventory_state_view_policy()

    configure_dnd_state_commands(router, view_policy=policy)
    configure_dnd_state_commands(router, view_policy=object())

    assert len(registered) == 1
    assert isinstance(registered[0], DndStateCommandMiddleware)
    assert registered[0].view_policy is policy
    assert router._upupa_dnd_state_view_policy is policy


def test_inventory_state_view_is_composed_without_state_command_monkeypatches():
    inventory_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "state_commands._inventory_items =" not in inventory_source
    assert "state_commands.render_inventory =" not in inventory_source
    assert "original_render_inventory" not in inventory_source
    assert "build_fun_inventory_state_view_policy()" in runtime_source
    assert "configure_dnd_state_commands(router, view_policy=state_view_policy)" in runtime_source
    assert runtime_source.index("configure_dnd_inventory_transfer(router)") < runtime_source.index(
        "configure_dnd_state_commands(router, view_policy=state_view_policy)"
    ) < runtime_source.index("completion.configure_dnd_completion(router, policy=completion_policy)")
