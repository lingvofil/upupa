from types import SimpleNamespace

from AI.dnd_state_commands import render_hero
from tests.test_dnd_state_commands import _active_session, _set_archive


def test_active_hero_hides_source_and_includes_live_inventory(monkeypatch):
    _set_archive(monkeypatch)
    session = _active_session()
    fake_dnd = SimpleNamespace(dnd_sessions={session.chat_id: session})

    hero = render_hero(fake_dnd, session.chat_id, 7, "Семён")

    assert "Источник:" not in hero
    assert "🎒 Инвентарь" in hero
    assert "• мокрый ключ" in hero
    assert "✨ Зуб Канцлера" in hero
    assert "ржавая ложка" not in hero


def test_saved_hero_hides_source_and_includes_saved_inventory(monkeypatch):
    _set_archive(monkeypatch)
    fake_dnd = SimpleNamespace(dnd_sessions={})

    hero = render_hero(fake_dnd, -100950, 7, "Семён")

    assert "Источник:" not in hero
    assert "🎒 Инвентарь" in hero
    assert "• ржавая ложка" in hero
    assert "✨ Корона Подъезда" in hero
