"""Регрессия этапа 3: состав и порядок регистрации хэндлеров контролируются явно."""
from tests import test_smoke_imports  # noqa: F401  (env + моки)

EXPECTED_TOTAL_HANDLERS = 182  # Мир Упупы: дубли роутеров удалены, добавлены битвы, блоки, дары, суд с защитой


def _count_handlers(router):
    return sum(len(obs.handlers) for obs in router.observers.values())


def test_total_handler_count():
    from handlers import ROUTERS
    total = sum(_count_handlers(r) for r in ROUTERS)
    assert total == EXPECTED_TOTAL_HANDLERS, (
        f"Хэндлеров {total}, ожидалось {EXPECTED_TOTAL_HANDLERS}. "
        "Если добавил/удалил хэндлер осознанно — обнови константу."
    )


def test_dialog_router_is_last():
    """Catch-all диалог обязан подключаться последним, иначе перехватит все команды."""
    from handlers import ROUTERS, dialog
    assert ROUTERS[-1] is dialog.router


def test_world_router_is_registered_before_dialog():
    from handlers import (
        ROUTERS,
        dialog,
        world_expansion,
        world_hub,
        world_interactions,
        world_symbols,
        world_visit_lifecycle,
        world_visit_media,
    )

    world = [
        world_visit_media.router,
        world_visit_lifecycle.router,
        world_interactions.router,
        world_expansion.router,
        world_symbols.router,
        world_hub.router,
    ]
    assert all(router in ROUTERS for router in world)
    positions = [ROUTERS.index(router) for router in world]
    # Reply-driven media showcases first, hub screens last; all before the catch-all.
    assert positions == sorted(positions)
    assert positions[-1] < ROUTERS.index(dialog.router)


def test_world_has_no_legacy_duplicate_routers():
    import importlib.util

    for legacy in ("handlers.world", "handlers.world_listing", "handlers.world_visit_decisions"):
        assert importlib.util.find_spec(legacy) is None, f"{legacy} must stay deleted"


def test_routers_count():
    from handlers import ROUTERS
    assert len(ROUTERS) == 30


def test_whatisthere_guard_does_not_match_pun_command():
    from types import SimpleNamespace
    from handlers.ai_vision import _contains_whatisthere_command

    message = SimpleNamespace(text="скаламбурь", caption=None)

    assert not _contains_whatisthere_command(message)
