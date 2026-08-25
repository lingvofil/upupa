"""Compatibility access to aiogram resources configured by the application.

Importing this module does not create Bot or Dispatcher instances. Stable
bot/dp proxies preserve legacy imports while the real objects are owned and
configured by app.bootstrap.
"""

from __future__ import annotations

from typing import Any, Callable

from aiogram import Bot, Dispatcher, Router


class AiogramResourceNotConfigured(RuntimeError):
    """A legacy proxy was used before the composition root configured it."""


_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def configure_aiogram_components(*, bot_instance: Bot, dispatcher: Dispatcher) -> None:
    global _bot, _dispatcher
    _bot = bot_instance
    _dispatcher = dispatcher


def reset_aiogram_components() -> None:
    """Clear configured objects. Intended for isolated tests."""
    global _bot, _dispatcher
    _bot = None
    _dispatcher = None


def get_bot() -> Bot:
    if _bot is None:
        raise AiogramResourceNotConfigured(
            "Bot is not configured; create the application first"
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    if _dispatcher is None:
        raise AiogramResourceNotConfigured(
            "Dispatcher is not configured; create the application first"
        )
    return _dispatcher


class _ResourceProxy:
    def __init__(self, name: str, getter: Callable[[], Any]) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_getter", getter)

    def unwrap(self):
        return object.__getattribute__(self, "_getter")()

    def __getattr__(self, name: str):
        return getattr(self.unwrap(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self.unwrap(), name, value)

    def __repr__(self) -> str:
        resource_name = object.__getattribute__(self, "_name")
        configured = (
            _bot is not None if resource_name == "bot" else _dispatcher is not None
        )
        state = "configured" if configured else "pending"
        return f"<AiogramResourceProxy {resource_name} ({state})>"


# Stable compatibility names for modules that still import bot/dp directly.
# No Bot or Dispatcher is built at import time.
bot = _ResourceProxy("bot", get_bot)
dp = _ResourceProxy("dispatcher", get_dispatcher)

# Router construction has no token or network side effects and remains
# compatible with historical decorator imports.
router = Router()


__all__ = [
    "AiogramResourceNotConfigured",
    "bot",
    "configure_aiogram_components",
    "dp",
    "get_bot",
    "get_dispatcher",
    "reset_aiogram_components",
    "router",
]
