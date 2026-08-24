import asyncio

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_external_comment_keeps_plain_text_and_raw_source_url(monkeypatch):
    from AI import summarize
    from features.channel import service

    source_url = "https://t.me/kapibara_fen/25376"
    source = {
        "channel": "kapibara_fen",
        "description": "@kapibara_fen",
        "owner": None,
        "allow_batya_reference": False,
    }

    async def fake_fetch_public_posts(channel: str, *, limit: int):
        assert channel == "kapibara_fen"
        assert limit == service.EXTERNAL_POSTS_LIMIT
        return [{"url": source_url, "text": "Медведев все-таки напоил chatgpt"}]

    async def fake_generate_with_active_model(prompt: str, chat_id: str):
        assert source_url not in prompt  # ссылка добавляется программой, не моделью
        return "Опять галлюцинации пошли."

    monkeypatch.setattr(service, "EXTERNAL_COMMENT_SOURCES", (source,))
    monkeypatch.setattr(service, "fetch_public_posts", fake_fetch_public_posts)
    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate_with_active_model)

    result = asyncio.run(service._try_generate_external_comment([], []))

    assert result is not None
    text, metadata = result
    assert text == f"{source_url}\n\nОпять галлюцинации пошли."
    assert metadata["external_source_channel"] == "@kapibara_fen"
    assert metadata["external_source_url"] == source_url
    assert metadata["post_kind"] == "external_comment"
