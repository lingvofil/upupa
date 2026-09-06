from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_short_birthday_command_and_legacy_aliases_are_accepted():
    from handlers.birthdays import _is_birthday_save_command

    assert _is_birthday_save_command(SimpleNamespace(text="мой др 1 апреля"))
    assert _is_birthday_save_command(SimpleNamespace(text="мой др 01.04"))
    assert _is_birthday_save_command(SimpleNamespace(text="упупа запомни: мой др 1 апреля"))
    assert _is_birthday_save_command(SimpleNamespace(text="упупа запомни мой др 1 апреля"))
    assert not _is_birthday_save_command(SimpleNamespace(text="он сказал мой др 1 апреля"))


def test_short_birthday_form_is_parseable():
    from AI.birthday_calendar import parse_birthday_date

    assert parse_birthday_date("мой др 1 апреля") == (1, 4)
    assert parse_birthday_date("мой др 01.04") == (1, 4)


def test_help_uses_short_birthday_example_without_placeholder_brackets():
    from prompts.help_texts import HELP_DICT, HELP_TEXT

    assert "<code>мой др 1 апреля</code>" in HELP_DICT["utils"]
    assert "упупа запомни: мой др" not in HELP_TEXT
    assert "мой др [" not in HELP_TEXT


def test_help_covers_current_public_command_families():
    from prompts.help_texts import HELP_TEXT

    expected = (
        # main / SMS
        "справка", "упупа настройки", "где сидишь", "смс [номер] [текст]",
        "ммс [номер] [текст]", "отключи смс", "включи смс",
        # talking / models / court
        "упупа не болтай", "упупа говори", "какой промпт", "поменяй промпт",
        "промпт участник", "какая модель", "упупа гемини", "упупа гигачат",
        "упупа грок", "упупа опен", "упупа силикон", "упупа нушо",
        "упупа скажи", "упупа умоляю", "переведи на", "упупа когда мы говорили",
        "упупа рассуди", "упупа суд", "судебная практика", "пиздиш",
        "чо по телеку", "новости футбола", "праздники", "очистка", "пирожок",
        "порошок",
        # stats / social / radio
        "мой лексикон", "лексикон чат", "моя статистика", "статистика чат",
        "кто я", "что за чат", "чобыло", "что я пропустил", "комикс",
        "радио упупы", "соцграф", "мои связи", "центровой",
        # World
        "упупа миры", "государство", "государства", "дипломатия", "карта мира",
        "мировые новости", "хроника", "санкции", "упупа санкции",
        "упупа снять санкции", "международный суд", "упупа международный суд",
        "упупа предложи союз", "упупа разорви союз", "упупа назови союз",
        "упупа объяви войну", "упупа прекрати войну", "упупа назначь посла",
        "упупа сними посла",
        # games
        "викторина", "викторина участники", "егра", "кракадил",
        "кракадил стоп", "кракадил наоборот", "кракадил художники",
        "упупа начни историю", "упупа заверши историю", "упупа закончи историю",
        # media
        "чотам", "опиши", "опиши сильно", "нарисуй", "сгенерируй",
        "отредактируй", "перерисуй", "добавь", "скаламбурь", "магшот",
        "нвидиа", "мем", "дисторшн", "пуп", "быстрее", "медленнее",
        "наоборот", "упупа сними", "оживи", "котогиф", "гиф [запрос]",
        "найди [запрос]",
        # utilities
        "упупа погода", "погода неделя", "мой др 1 апреля",
        "упупа дни рождения", "туры [запрос]", "отели [запрос]",
        "билеты [запрос]", "упупа ищи", "имя [имя]", "кем стать",
    )

    missing = [command for command in expected if command not in HELP_TEXT]
    assert missing == [], f"Справка потеряла публичные команды: {missing}"


def test_help_does_not_advertise_disabled_chogovoryat_handler():
    from prompts.help_texts import HELP_TEXT

    assert "чоговорят" not in HELP_TEXT
