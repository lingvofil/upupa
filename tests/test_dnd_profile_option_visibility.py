from AI import dnd_campaign as campaign


def test_profile_choices_keep_full_text_outside_inline_buttons():
    options = [
        "Алкоголичка-медиум, общающаяся с призраками через пустые бутылки",
        "Поехавший часовщик, который чинит время и ломает всё остальное",
        "Нервная библиотекарша, пожирающая запрещённые книги после полуночи",
        "Одноглазая воровка, коллекционирующая проклятые ключи от чужих дверей",
        "Забытая принцесса, танцующая на могилах собственных поклонников",
    ]

    text = campaign._profile_choice_text("style", options)
    keyboard = campaign._profile_keyboard(42, "style", options)

    assert text.startswith("🎭 Выбери образ/стиль:\n\n")
    for index, option in enumerate(options, start=1):
        assert f"{index}. {option}" in text

    assert [button.text for button in keyboard.inline_keyboard[0]] == ["1", "2", "3", "4", "5"]
    assert [button.callback_data for button in keyboard.inline_keyboard[0]] == [
        f"dnd:prof:42:style:{index}" for index in range(5)
    ]
    assert keyboard.inline_keyboard[1][0].text == "🎲 Ещё варианты"


def test_profile_choice_text_supports_next_step_and_regeneration_headings():
    options = ["вариант один", "вариант два", "вариант три", "вариант четыре", "вариант пять"]

    assert campaign._profile_choice_text(
        "strength", options, heading="🎭 Теперь выбери"
    ).startswith("🎭 Теперь выбери сильную сторону:")
    assert campaign._profile_choice_text(
        "weakness", options, heading="🎲 Новая пачка. Выбери"
    ).startswith("🎲 Новая пачка. Выбери слабость:")
