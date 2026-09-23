import asyncio
from types import SimpleNamespace

from services import nameinfo


def test_process_name_info_includes_expanded_profile(monkeypatch):
    async def fake_get_name_info(name):
        return {
            "name": name,
            "age": 47,
            "gender": "женский",
            "gender_probability": 0.98,
            "nationality": "RU (75%), KZ (22%)",
        }

    async def fake_generate_name_profile(name, chat_id):
        assert name == "Альбина"
        assert chat_id == -100123
        return (
            "Имя - Альбина. Имя обычно связывают с латинским albus — «белый», «светлый».\n\n"
            "📜 Происхождение и история\n\n"
            "· Восходит к латинской антропонимической традиции.\n\n"
            "🌍 Распространение в мире\n\n"
            "Имя встречается в странах Европы и СНГ.\n\n"
            "💡 Интересные факты\n\n"
            "· Существуют разные национальные формы имени."
        )

    monkeypatch.setattr(nameinfo, "get_name_info", fake_get_name_info)
    monkeypatch.setattr(nameinfo, "generate_name_profile", fake_generate_name_profile)

    message = SimpleNamespace(
        text="имя Альбина",
        chat=SimpleNamespace(id=-100123),
    )

    success, response = asyncio.run(nameinfo.process_name_info(message))

    assert success is True
    assert response.startswith("Имя - Альбина.")
    assert "📜 Происхождение и история" in response
    assert "🌍 Распространение в мире" in response
    assert "💡 Интересные факты" in response
    assert response.endswith(
        "Возраст - 47\n"
        "Пол - женский (вероятность: 98%)\n"
        "Национальность - RU (75%), KZ (22%)"
    )
    assert response.count("Имя - Альбина") == 1


def test_process_name_info_keeps_old_format_when_profile_generation_fails(monkeypatch):
    async def fake_get_name_info(name):
        return {
            "name": name,
            "age": 31,
            "gender": "мужской",
            "gender_probability": 0.91,
            "nationality": "RU (80%), BY (10%)",
        }

    async def fake_generate_name_profile(name, chat_id):
        return None

    monkeypatch.setattr(nameinfo, "get_name_info", fake_get_name_info)
    monkeypatch.setattr(nameinfo, "generate_name_profile", fake_generate_name_profile)

    message = SimpleNamespace(
        text="имя Женя",
        chat=SimpleNamespace(id=-100123),
    )

    success, response = asyncio.run(nameinfo.process_name_info(message))

    assert success is True
    assert response == (
        "Имя - Женя\n"
        "Возраст - 31\n"
        "Пол - мужской (вероятность: 91%)\n"
        "Национальность - RU (80%), BY (10%)"
    )
