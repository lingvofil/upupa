from AI import dnd_profile_gender_grounding as feature


def test_gender_keyboard_has_two_explicit_choices_without_regeneration():
    markup = feature._gender_keyboard(42)
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert [button.text for button in buttons] == ["♂️ Мужской", "♀️ Женский"]
    assert [button.callback_data for button in buttons] == [
        "dnd:prof:42:gender:0",
        "dnd:prof:42:gender:1",
    ]


def test_gender_normalizer_accepts_short_russian_and_english_variants():
    assert feature._normalize_gender("М") == "мужской"
    assert feature._normalize_gender("женщина") == "женский"
    assert feature._normalize_gender("female") == "женский"
    assert feature._normalize_gender("эльф") is None


def test_metaphysical_plot_guard_rejects_reality_breaks_and_doubles():
    forbidden = (
        "Разрыв реальности выпускает двойников героев на улицы города.",
        "Партия узнаёт, что весь мир был симуляцией.",
        "Из параллельной реальности приходит копия главного героя.",
        "Мастер игры появляется внутри мира и ломает четвёртую стену.",
    )
    allowed = (
        "Банда гоблинов украла городской водопровод и требует выкуп.",
        "Проклятый маяк каждую ночь заманивает корабли на скалы.",
        "Воин наносит двойной удар по воротам и ломает засов.",
    )

    assert all(feature._metaphysical_plot(text) for text in forbidden)
    assert not any(feature._metaphysical_plot(text) for text in allowed)


def test_grounding_rule_is_a_hard_ban_not_a_rare_twist_exception():
    text = feature.GROUNDING_RULES.casefold()

    assert "не уводи историю в метафизику" in text
    assert "двойники" in text
    assert "не используй эти тропы даже как редкий неожиданный поворот" in text
    assert "допустимо" not in text
