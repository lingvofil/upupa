from AI.dnd_style import DND_STYLE_INSTRUCTION, errative_text


def test_dnd_style_prioritizes_free_party_turns():
    assert "примерно в половине" in DND_STYLE_INSTRUCTION
    assert "около каждого второго хода" in DND_STYLE_INSTRUCTION
    assert "ВСЕГДА предпочитай ACTION:INPUT" in DND_STYLE_INSTRUCTION
    assert "Не делай длинную цепочку ROLL/POLL" in DND_STYLE_INSTRUCTION


def test_dnd_style_uses_erratives_and_insults():
    text = errative_text(
        "Игра с участниками. Игроки, пишите действия и завершить можно позже.",
        add_insult=True,
    )

    assert "Егра" in text
    assert "учаснег" in text
    assert "Егроки" in text
    assert "пешите" in text
    assert "дейсвия" in text
    assert "завиршить" in text
    assert any(word in text for word in ("дегенераты", "мудилы", "кретины", "долбоёбы"))


def test_dnd_style_keeps_mechanical_terms_readable():
    text = errative_text(
        "Сложность — 12. Команда «дальше». КРИТИЧЕСКАЯ УДАЧА.",
        add_insult=True,
    )

    assert "Сложность — 12" in text
    assert "дальше" in text
    assert "КРИТИЧЕСКАЯ УДАЧА" in text
