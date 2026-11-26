"""Plain-text view of Telegram messages for dialogue and reply context."""


def _rich_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_rich_text(item) for item in value)
    fields = _as_dict(value)
    content = fields.get("text") or fields.get("alternative_text") or fields.get("expression")
    return _rich_text(content) if content else ""


def _rich_block_text(block: dict) -> str:
    lines = [
        _rich_text(block.get("summary")),
        _rich_text(block.get("text")),
        _rich_text(block.get("expression")),
    ]
    lines.extend(_rich_block_text(child) for child in block.get("blocks", []))
    lines.extend(_rich_block_text(item) for item in block.get("items", []))
    lines.extend(" | ".join(_rich_text(cell) for cell in row) for row in block.get("cells", []))
    lines.append(_rich_text(block.get("caption")))
    return "\n".join(line for line in lines if line.strip())


def _as_dict(value) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value if isinstance(value, dict) else {}


def telegram_message_text(message) -> str:
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if text:
        return text

    # aiogram 3.24 keeps new Bot API fields in Pydantic extras. Attribute
    # access also works once aiogram gives rich_message a declared model.
    rich_message = _as_dict(getattr(message, "rich_message", None))
    return "\n".join(
        text for block in rich_message.get("blocks", []) if (text := _rich_block_text(block))
    )[:4096]


def telegram_quote_text(quote) -> str:
    text = (getattr(quote, "text", None) or "").strip()
    return text or "\n".join(
        _rich_text(fragment) for fragment in (getattr(quote, "rich_texts", None) or [])
    ).strip()
