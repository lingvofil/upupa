"""Регрессия этапа 3: распил main.py не должен менять
состав и порядок регистрации хэндлеров.
"""
from tests import test_smoke_imports  # noqa: F401  (env + моки)

EXPECTED_TOTAL_HANDLERS = 87  # столько было в монолитном main.py


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


def test_routers_count():
    from handlers import ROUTERS
    assert len(ROUTERS) == 14
