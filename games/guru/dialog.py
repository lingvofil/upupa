"""Состояние компактного диалога в мессенджере. Без Telegram и правил игры."""

from dataclasses import dataclass, field
import re


# Запас под навигацию и HTML. Фрагменты уже экранированы адаптером textui.
PAGE_LIMIT = 3400


@dataclass
class Dialog:
    history: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    rows: list[list[tuple[str, str]]] = field(default_factory=list)
    media_id: int | None = None
    media_key: str = ""


def _size(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _split(part: str) -> list[str]:
    """Очень длинную реплику разбить с закрытием/повторным открытием HTML-тегов."""
    result, stack = [], []
    current, size = "", 0
    for token in re.findall(r"</?[^>]+>|&[^;]+;|.", part, re.DOTALL):
        closing = "".join(f"</{tag}>" for tag in reversed(stack))
        if size + _size(token) + _size(closing) > PAGE_LIMIT:
            result.append(current + closing)
            current = "".join(f"<{tag}>" for tag in stack)
            size = _size(current)
        current += token
        size += _size(token)
        if token.startswith("</"):
            stack.pop()
        elif token.startswith("<"):
            stack.append(token[1:-1])
    if current:
        result.append(current)
    return result


def pages(parts: list[str]) -> list[str]:
    """Разбить диалог между целыми репликами, сохраняя HTML-разметку."""
    result: list[str] = []
    current = ""
    for original in parts:
        if not original:
            continue
        for part in _split(original) if _size(original) > PAGE_LIMIT else [original]:
            if current and _size(current) + _size(part) + 2 > PAGE_LIMIT:
                result.append(current)
                current = ""
            current = f"{current}\n\n{part}" if current else part
    if current:
        result.append(current)
    return result or ["🕯 Салон"]
