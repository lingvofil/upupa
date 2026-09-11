from types import SimpleNamespace

from AI import dnd_campaign
from AI.dnd_state_commands import render_status


def test_completed_status_is_split_into_readable_sections(monkeypatch):
    latest = {
        "selected_plot": "Почтамт объявил войну адресатам",
        "finale": "Почтамт сгорел, письма победили.",
        "epilogue": "Герои получили пожизненную подписку на спам.",
        "threat": {"name": "Почтовый бунт", "level": 4, "max": 6},
        "scenes": ["Последняя архивная сцена"],
    }
    monkeypatch.setattr(dnd_campaign, "_load_archive", lambda _dnd: None)
    monkeypatch.setattr(dnd_campaign, "_latest_campaign", lambda _chat_id: latest)

    status = render_status(SimpleNamespace(dnd_sessions={}), -100950)

    assert status == (
        "🧭 Что происходит?\n\n"
        "Активной егры сейчас нет.\n"
        "Показываю последнюю завершённую.\n\n"
        "🎬 Сюжет\n"
        "Почтамт объявил войну адресатам\n\n"
        "🏁 Финал\n"
        "Почтамт сгорел, письма победили.\n\n"
        "📚 Эпилог\n"
        "Герои получили пожизненную подписку на спам.\n\n"
        "⚠️ Угроза: Почтовый бунт — ■■■■□□ 4/6"
    )


def test_active_status_uses_the_same_section_layout(monkeypatch):
    session = SimpleNamespace(
        state="WAITING_ACTION",
        selected_plot="Санаторий теряет этажи",
        scene_log=["Лифт выплюнул героев прямо на крышу столовой."],
        threat={"name": "Налоговая кавалерия", "level": 3, "max": 6},
        pending_actions={},
        action_target_user_ids=[],
    )
    fake_dnd = SimpleNamespace(dnd_sessions={-100950: session})
    monkeypatch.setattr(dnd_campaign, "_load_archive", lambda _dnd: None)
    monkeypatch.setattr(dnd_campaign, "_ensure", lambda _session: None)

    status = render_status(fake_dnd, -100950)

    assert "Егра сейчас активна.\n\n🎬 Сюжет\nСанаторий теряет этажи" in status
    assert "\n\n📍 Последняя сцена\nЛифт выплюнул героев" in status
    assert "\n\n⚠️ Угроза: Налоговая кавалерия — ■■■□□□ 3/6" in status
