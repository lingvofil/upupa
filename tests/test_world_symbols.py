from datetime import datetime, timezone

import pytest

from features.world.ledger import WorldDetails
from features.world.models import WorldState
from features.world.symbols import WorldSymbolStore, build_state_symbol_prompt
from infrastructure.persistence.sqlite_world import SQLiteWorldRepository


def _state() -> WorldState:
    return WorldState(
        world_id=7,
        chat_id=-1007,
        title="Республика Трёх Табуреток",
        created_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        enabled=True,
    )


def _details() -> WorldDetails:
    return WorldDetails(
        world_id=7,
        government_form="мемократическая федерация",
        climate="тёплый ламповый с локальными грозами",
        main_threat="голосовые по семь минут",
    )


def test_flag_prompt_forces_actual_flag_and_visual_joke():
    prompt = build_state_symbol_prompt(
        _state(),
        _details(),
        "flag",
        idea="опоссум держит бокал",
    )

    assert "ИМЕННО ГОСУДАРСТВЕННЫЙ ФЛАГ" in prompt
    assert "прямоугольного флага примерно 3:2" in prompt
    assert "Не рисуй древко" in prompt
    assert "абсурдным или сатирическим визуальным приколом" in prompt
    assert "опоссум держит бокал" in prompt
    assert "голосовые по семь минут" in prompt
    assert "Не добавляй текст" in prompt


def test_emblem_prompt_forces_heraldry_not_scene():
    prompt = build_state_symbol_prompt(_state(), _details(), "emblem")

    assert "ИМЕННО ГОСУДАРСТВЕННЫЙ ГЕРБ" in prompt
    assert "щит" in prompt
    assert "без окружения" in prompt
    assert "Не рисуй здание" in prompt


def test_symbol_store_persists_and_replaces_current_symbol(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    state = repo.enable_state(-1007, "Республика Трёх Табуреток")
    store = WorldSymbolStore(repo.path)

    first = store.set_symbol(state.world_id, "flag", "telegram-flag", "flag prompt")
    assert first.kind == "flag"
    assert store.get_symbol(state.world_id) == first

    second = store.set_symbol(state.world_id, "emblem", "telegram-emblem", "emblem prompt")
    loaded = store.get_symbol(state.world_id)
    assert loaded is not None
    assert loaded.kind == "emblem"
    assert loaded.telegram_file_id == "telegram-emblem"
    assert loaded.prompt == "emblem prompt"


def test_symbol_store_rejects_unknown_kind(tmp_path):
    repo = SQLiteWorldRepository(tmp_path / "world.db")
    repo.init_schema()
    state = repo.enable_state(-1007, "Республика Трёх Табуреток")
    store = WorldSymbolStore(repo.path)

    with pytest.raises(ValueError):
        store.set_symbol(state.world_id, "banner", "file", "prompt")
