"""Регрессия этапа 3: распил main.py не должен менять
состав и порядок регистрации хэндлеров.
"""
from tests import test_smoke_imports  # noqa: F401  (env + моки)

EXPECTED_TOTAL_HANDLERS = 126  # + интерактивный Мир Упупы v2


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
    from handlers import ROUTERS, dialog, world, world_hub, world_listing

    assert world_hub.router in ROUTERS
    assert world_listing.router in ROUTERS
    assert world.router in ROUTERS
    assert ROUTERS.index(world_hub.router) < ROUTERS.index(world_listing.router)
    assert ROUTERS.index(world_listing.router) < ROUTERS.index(world.router)
    assert ROUTERS.index(world.router) < ROUTERS.index(dialog.router)


def test_routers_count():
    from handlers import ROUTERS
    assert len(ROUTERS) == 21


def test_whatisthere_guard_does_not_match_pun_command():
    from types import SimpleNamespace
    from handlers.ai_vision import _contains_whatisthere_command

    message = SimpleNamespace(text="скаламбурь", caption=None)

    assert not _contains_whatisthere_command(message)
