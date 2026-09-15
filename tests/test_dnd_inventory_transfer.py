import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from AI import dnd_campaign
from AI.dnd_inventory_fun import (
    DndInventoryTransferMiddleware,
    FUN_INVENTORY_RULES,
    InventoryTransferError,
    _artifact_progress_line,
    apply_stackable_metadata,
    configure_dnd_inventory_transfer,
    parse_transfer_command,
    transfer_between_inventories,
    transfer_inventory,
)


ROOT = Path(__file__).resolve().parents[1]


def _session():
    session = SimpleNamespace(
        chat_id=-100,
        mode="participants",
        state="WAITING_ACTION",
        participants={
            "1": {"user_id": 1, "name": "Детектор"},
            "2": {"user_id": 2, "name": "Морковка"},
        },
        inventories={},
        scene_count=0,
    )
    dnd_campaign._ensure(session)
    return session


class FakeTransferMessage:
    def __init__(self, text, *, sender_id=1, target=None):
        self.text = text
        self.chat = SimpleNamespace(id=-100)
        self.from_user = SimpleNamespace(id=sender_id, first_name="Детектор")
        self.reply_to_message = SimpleNamespace(from_user=target) if target is not None else None
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


def test_parse_transfer_command_supports_quantity_and_alias():
    assert parse_transfer_command("передать Ботинок Истины") == (1, "Ботинок Истины")
    assert parse_transfer_command("Упупа отдать 2 штрафа") == (2, "штрафа")
    assert parse_transfer_command("я передаю ключ") is None


def test_transfer_moves_part_of_stack_and_merges_target_stack():
    inventories = {
        "1": [{"name": "штраф", "few": "штрафа", "many": "штрафов", "kind": "item", "quantity": 5}],
        "2": [{"name": "штраф", "few": "штрафа", "many": "штрафов", "kind": "item", "quantity": 2}],
    }

    display, kind = transfer_between_inventories(inventories, 1, 2, "штрафа", 2)

    assert display == "2 штрафа"
    assert kind == "item"
    assert inventories["1"][0]["quantity"] == 3
    assert inventories["2"][0]["quantity"] == 4


def test_transfer_moves_unique_artifact_whole():
    inventories = {
        "1": [{"name": "Ботинок Истины", "kind": "artifact"}],
        "2": [],
    }

    display, kind = transfer_between_inventories(inventories, 1, 2, "ботинок истины")

    assert display == "Ботинок Истины"
    assert kind == "artifact"
    assert inventories["1"] == []
    assert inventories["2"] == [{"name": "Ботинок Истины", "kind": "artifact"}]


def test_transfer_rejects_missing_quantity_and_duplicate_artifact():
    inventories = {
        "1": [{"name": "штраф", "kind": "item", "quantity": 2}],
        "2": [],
    }
    with pytest.raises(InventoryTransferError, match="Столько нет"):
        transfer_between_inventories(inventories, 1, 2, "штраф", 3)

    inventories = {
        "1": [{"name": "Ботинок Истины", "kind": "artifact"}],
        "2": [{"name": "Ботинок Истины", "kind": "artifact"}],
    }
    with pytest.raises(InventoryTransferError, match="уже есть"):
        transfer_between_inventories(inventories, 1, 2, "Ботинок Истины")


def test_new_artifact_is_tracked_as_current_campaign_award():
    session = _session()
    before_text = _artifact_progress_line(session)
    assert "пока 0" in before_text

    apply_stackable_metadata(
        dnd_campaign,
        dnd_campaign._apply_metadata,
        session,
        "[ITEM:ADD;PLAYER:1;NAME:Ключ от жопы мира;KIND:artifact]",
    )

    assert session.artifact_awards == {"1": ["Ключ от жопы мира"]}
    assert "Ключ от жопы мира" in _artifact_progress_line(session)
    assert "ВО ВРЕМЯ игры" in FUN_INVENTORY_RULES
    assert "Не откладывай выдачу до эпилога" in FUN_INVENTORY_RULES


def test_archive_transfer_reassigns_persistent_artifact(monkeypatch):
    archive = {
        "version": 1,
        "chats": {
            "-100": {
                "campaigns": [],
                "players": {
                    "1": {
                        "name": "Детектор",
                        "inventory": [{"name": "Ботинок Истины", "kind": "artifact"}],
                        "artifacts": [{"name": "Ботинок Истины", "kind": "artifact"}],
                    },
                    "2": {
                        "name": "Морковка",
                        "inventory": [],
                        "artifacts": [],
                    },
                },
            }
        },
    }
    monkeypatch.setattr(dnd_campaign, "_archive", archive)
    monkeypatch.setattr(dnd_campaign, "_archive_loaded", True)
    monkeypatch.setattr(dnd_campaign, "_save_archive", lambda _dnd: None)
    fake_dnd = SimpleNamespace(dnd_sessions={})

    display, kind, source = transfer_inventory(fake_dnd, -100, 1, 2, "Ботинок Истины")

    assert (display, kind, source) == ("Ботинок Истины", "artifact", "archive")
    players = archive["chats"]["-100"]["players"]
    assert players["1"]["inventory"] == []
    assert players["1"]["artifacts"] == []
    assert players["2"]["inventory"] == [{"name": "Ботинок Истины", "kind": "artifact"}]
    assert players["2"]["artifacts"] == [{"name": "Ботинок Истины", "kind": "artifact"}]


def test_transfer_middleware_delegates_non_transfer_messages():
    message = FakeTransferMessage("осматриваю сундук")
    called = []

    async def handler(event, data):
        called.append((event, data))
        return "downstream"

    payload = {"marker": 1}
    result = asyncio.run(DndInventoryTransferMiddleware()(handler, message, payload))

    assert result == "downstream"
    assert called == [(message, payload)]
    assert message.answers == []


def test_transfer_middleware_consumes_transfer_without_reply():
    message = FakeTransferMessage("передать Ботинок Истины")
    called = []

    async def handler(_event, _data):
        called.append(True)
        return "downstream"

    result = asyncio.run(DndInventoryTransferMiddleware()(handler, message, {}))

    assert result is None
    assert called == []
    assert message.answers == [
        ("↪️ Ответь командой «передать <предмет>» на сообщение того, кому отдаёшь вещь.", {})
    ]


def test_transfer_middleware_registration_is_idempotent():
    registered = []
    router = SimpleNamespace(
        message=SimpleNamespace(outer_middleware=lambda middleware: registered.append(middleware))
    )

    configure_dnd_inventory_transfer(router)
    configure_dnd_inventory_transfer(router)

    assert len(registered) == 1
    assert isinstance(registered[0], DndInventoryTransferMiddleware)


def test_inventory_transfer_is_composed_without_state_middleware_class_patch():
    inventory_source = (ROOT / "AI" / "dnd_inventory_fun.py").read_text(encoding="utf-8")
    runtime_source = (ROOT / "AI" / "dnd_runtime.py").read_text(encoding="utf-8")

    assert "DndStateCommandMiddleware.__call__" not in inventory_source
    assert "original_state_middleware_call" not in inventory_source
    assert "configure_dnd_inventory_transfer(router)" in runtime_source
    assert runtime_source.index("configure_dnd_inventory_transfer(router)") < runtime_source.index(
        "completion.configure_dnd_completion(router, policy=completion_policy)"
    )
