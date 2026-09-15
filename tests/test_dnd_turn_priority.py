import asyncio
from types import SimpleNamespace

from AI import dnd_turn_priority as priority


def test_group_actions_are_repeated_at_end_of_enriched_prompt():
    captured = []

    async def base_generate(_session, prompt):
        captured.append(prompt)
        return "ok"

    dnd = SimpleNamespace(generate_session_response=base_generate)
    priority.configure_dnd_turn_priority(dnd)
    session = SimpleNamespace(chat_id=-1001)
    prompt = (
        "Игроки заявили действия одновременно:\n"
        "- Детектор: выпить со сплыной\n"
        "- M&M: ахуеваю по вертолётён Шевелитесь, пока сюжет не сдох\n"
        "Разреши их в одной общей сцене: учти последствия.\n\n"
        "РЕЖИССЁР СЦЕНЫ: опасность.\n\n"
        "КАМПАНИЯ: старая крыса всё ещё рядом."
    )

    result = asyncio.run(dnd.generate_session_response(session, prompt))

    assert result == "ok"
    sent = captured[0]
    assert sent.endswith(
        "ТЕКУЩИЕ ДЕЙСТВИЯ:\n"
        "- Детектор: выпить со сплыной\n"
        "- M&M: ахуеваю по вертолётён Шевелитесь, пока сюжет не сдох"
    )
    assert sent.rfind("ТЕКУЩИЕ ДЕЙСТВИЯ") > sent.rfind("старая крыса")
    assert "Сначала разреши КАЖДОЕ" in sent
    assert "Не продолжай вместо этого действия из прошлого хода" in sent


def test_non_group_generation_is_unchanged():
    captured = []

    async def base_generate(_session, prompt):
        captured.append(prompt)
        return "ok"

    dnd = SimpleNamespace(generate_session_response=base_generate)
    priority.configure_dnd_turn_priority(dnd)
    session = SimpleNamespace(chat_id=-1001)

    asyncio.run(dnd.generate_session_response(session, "Выбранный сюжет: порт. Начинай."))

    assert captured == ["Выбранный сюжет: порт. Начинай."]
