import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


def test_help_main_page_advertises_upupa_channel():
    from prompts.help_texts import HELP_DICT

    assert "@upupa_channel - мой конал" in HELP_DICT["main"]


def test_help_main_page_links_to_site():
    from prompts.help_texts import HELP_DICT

    assert '<a href="https://lingvofil.github.io/upupa/">' in HELP_DICT["main"]
    assert "мой сайд" in HELP_DICT["main"]


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
        # talking / court
        "упупа не болтай", "упупа говори", "какой промпт", "поменяй промпт",
        "случайный промпт", "расскажи промпт",
        "промпт участник",
        "упупа скажи", "упупа умоляю", "переведи на", "упупа когда мы говорили",
        "упупа рассуди", "упупа суд", "судебная практика", "пиздиш",
        "чо по телеку", "новости футбола", "праздники", "очистка", "пирожок",
        "порошок",
        # stats / social / radio
        "мой лексикон", "лексикон чат", "моя статистика", "статистика чат",
        "кто я", "что за чат", "чобыло", "что я пропустил", "комикс",
        "радио упупы", "соцграф", "мои связи", "центровой", "летопись",
        "отношения @user", "мои отношения", "отношения чата", "история @user",
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
        "гуру", "гуру смена", "гуру сеанс", "гуру людмила", "гуру приём",
        "гуру карта", "гуру касса", "гуру лавка", "гуру салон",
        "гуру полка", "гуру салоны", "гуру стоп",
        "упупа днд", "днд старт", "герой", "днд герой", "инвентарь",
        "днд инвентарь", "передать [предмет]", "отдать [предмет]", "днд связи",
        "днд сюжет", "днд конец",
        # media
        "чотам", "опиши", "нарисуй", "перерисуй", "добавь", "скаламбурь", "магшот",
        "нвидиа", "мем", "дисторшн", "пуп", "быстрее", "медленнее",
        "наоборот", "упупа сними", "оживи", "котогиф", "гиф [запрос]",
        "найди [запрос]",
        # utilities
        "упупа погода", "погода неделя", "гороскоп", "мой др 1 апреля",
        "упупа дни рождения", "имя [имя]", "кем стать",
    )

    missing = [command for command in expected if command not in HELP_TEXT]
    assert missing == [], f"Справка потеряла публичные команды: {missing}"


def test_help_does_not_advertise_owner_only_commands():
    from prompts.help_texts import HELP_TEXT

    # Туры, отели, билеты и поиск аккаунтов владелец делал для себя: команды
    # работают, но в справке и на сайте их нет.
    for command in ("туры [запрос]", "отели [запрос]", "билеты [запрос]", "упупа ищи"):
        assert command not in HELP_TEXT
    assert "ПОЕЗДОЧКИ" not in HELP_TEXT


def test_help_does_not_advertise_disabled_chogovoryat_handler():
    from prompts.help_texts import HELP_TEXT

    assert "чоговорят" not in HELP_TEXT


def test_help_explains_public_guru_game_and_group_only_live_mode():
    from prompts.help_texts import HELP_DICT

    section = HELP_DICT["guru"]
    assert "<b>🔮 ГУРУ" in section
    assert "<code>гуру</code>" in section
    assert "<code>гуру играть</code>" in section
    assert "Таро, ладонь и гороскоп" in section
    assert "только в группе" in section
    assert "Игра открыта в личке и чатах; одна партия на чат." in section


def test_help_relocates_sections_and_hides_selected_commands():
    from prompts.help_texts import HELP_DICT, HELP_TEXT

    assert "<code>гуру</code>" in HELP_DICT["main"]
    assert "<code>упупа миры</code>" not in HELP_DICT["main"]
    assert "упупа настройки</code> → «Вкл. Мир»" in HELP_DICT["world"].splitlines()[1]
    assert "<code>упупа миры</code>" in HELP_DICT["world"].splitlines()[2]
    for command in ("чо по телеку", "новости футбола", "праздники",
                    "упупа рассуди", "упупа суд", "судебная практика"):
        assert f"<code>{command}</code>" in HELP_DICT["utils"]
        assert f"<code>{command}</code>" not in HELP_DICT["talking"]
    for command in ("какая модель", "упупа гемини", "упупа гигачат", "упупа грок",
                    "упупа опен", "упупа силикон", "упупа нушо", "опиши сильно",
                    "сгенерируй [промпт]", "отредактируй [что сделать]"):
        assert f"<code>{command}</code>" not in HELP_TEXT


def test_help_navigates_from_main_through_each_game_and_back():
    from features.interactive_settings import handle_help_callback, send_help_menu
    from prompts import HELP_DICT

    message = SimpleNamespace(
        answer=AsyncMock(), text=None, caption=None, edit_text=AsyncMock()
    )
    query = SimpleNamespace(message=message, answer=AsyncMock())

    def buttons(sent):
        return [
            button.callback_data
            for row in sent.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]

    asyncio.run(send_help_menu(message))
    assert message.answer.call_args.kwargs["text"] == HELP_DICT["main"]
    assert "help:creative" in buttons(message.answer.call_args)

    query.data = "help:creative"
    asyncio.run(handle_help_callback(query))
    games_page = message.edit_text.call_args
    assert games_page.kwargs["text"] == HELP_DICT["creative"]
    assert games_page.kwargs["text"].count("<code>") == 5
    assert buttons(games_page) == [
        "help:crocodile", "help:guru", "help:dnd", "help:quiz", "help:main"
    ]

    for path in buttons(games_page)[:-1]:
        query.data = path
        asyncio.run(handle_help_callback(query))
        game_page = message.edit_text.call_args
        assert game_page.kwargs["text"] == HELP_DICT[path.split(":")[1]]
        assert buttons(game_page) == ["help:creative", "help:main"]

        query.data = "help:creative"
        asyncio.run(handle_help_callback(query))
        assert message.edit_text.call_args == games_page

    query.data = "help:main"
    asyncio.run(handle_help_callback(query))
    assert message.edit_text.call_args.kwargs["text"] == HELP_DICT["main"]
