"""Task-local factual context injected into standalone AI prompts.

ContextVar keeps concurrent chats isolated. Callers set context only around the
specific command invocation and always reset it afterwards.
"""

from __future__ import annotations

from contextvars import ContextVar, Token


_prompt_context: ContextVar[str] = ContextVar("upupa_prompt_context", default="")


def set_prompt_context(text: str | None) -> Token:
    return _prompt_context.set((text or "").strip())


def reset_prompt_context(token: Token) -> None:
    _prompt_context.reset(token)


def get_prompt_context() -> str:
    return _prompt_context.get()
