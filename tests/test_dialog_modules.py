import ast
from pathlib import Path

from tests import test_smoke_imports  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _imported_modules(path: str) -> set[str]:
    tree = ast.parse(_source(path), filename=path)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_focused_dialog_modules_are_self_contained():
    for path in (
        "AI/dialog/generation.py",
        "AI/dialog/model_commands.py",
        "AI/dialog/prompt_commands.py",
        "AI/dialog/serious_mode.py",
        "AI/dialog/settings.py",
        "AI/dialog/style.py",
    ):
        imports = _imported_modules(path)
        assert "AI.talking" not in imports


def test_dialog_settings_preserve_legacy_defaults():
    from AI.dialog import settings

    chat_id = "r7-test-defaults"
    settings.chat_settings.pop(chat_id, None)
    settings.update_chat_settings(chat_id)

    assert settings.chat_settings[chat_id]["dialog_enabled"] is True
    assert settings.chat_settings[chat_id]["reactions_enabled"] is True
    assert settings.chat_settings[chat_id]["prompt_name"] == "летописец"
    assert settings.chat_settings[chat_id]["prompt_source"] == "daily"
    assert settings.chat_settings[chat_id]["active_model"] == "gemini"

    settings.chat_settings.pop(chat_id, None)


def test_poem_active_users_are_ranked_by_recent_activity():
    from AI.dialog.prompt_commands import _rank_active_poem_users

    users = {
        "1": {"weekly": 4, "daily": 4, "total": 100},
        "2": {"weekly": 9, "daily": 0, "total": 20},
        "3": {"weekly": 9, "daily": 3, "total": 10},
        "4": {"weekly": 0, "daily": 0, "total": 200},
    }

    assert _rank_active_poem_users(users, limit=4) == ["3", "2", "1"]


def test_poem_active_users_fall_back_to_lifetime_counts():
    from AI.dialog.prompt_commands import _rank_active_poem_users

    users = {
        "1": {"weekly": 0, "daily": 0, "total": 5},
        "2": {"weekly": 0, "daily": 0, "total": 20},
    }

    assert _rank_active_poem_users(users) == ["2", "1"]


def test_poem_dynamic_characters_use_six_slots_and_mix_optional_bot(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from AI.dialog import prompt_commands

    async def valid_users(chat_id):
        assert chat_id == "-1001"
        return {
            "11": {"weekly": 10, "daily": 4, "total": 110},
            "22": {"weekly": 9, "daily": 3, "total": 100},
            "33": {"weekly": 8, "daily": 2, "total": 90},
            "44": {"weekly": 7, "daily": 2, "total": 80},
            "55": {"weekly": 6, "daily": 1, "total": 70},
            "88": {"weekly": 5, "daily": 1, "total": 60},
            "99": {"weekly": 4, "daily": 1, "total": 50},
        }

    names = {
        11: "Света",
        22: "Алина",
        33: "Детектор",
        44: "Ольга",
        55: "Никита",
        88: "Жека",
        99: "Софико",
    }

    async def display_name(chat_id, user_id):
        assert chat_id == -1001
        return names[user_id]

    async def participant_activity(chat_id, *, period_hours, limit):
        assert chat_id == -1001
        return [
            {"user_id": 66, "message_count": 15, "user_name": "Карл"},
            {"user_id": 77, "message_count": 9, "user_name": "Сглыпа"},
        ]

    async def get_me():
        return SimpleNamespace(first_name="Upupa Epops", full_name="Upupa Epops")

    async def get_chat_member(chat_id, user_id):
        bot_names = {66: "Карл", 77: "Сглыпа"}
        return SimpleNamespace(
            user=SimpleNamespace(
                is_bot=True,
                first_name=bot_names[user_id],
                full_name=bot_names[user_id],
            )
        )

    def put_bot_in_middle(values):
        bot_name = values.pop()
        values.insert(2, bot_name)

    monkeypatch.setattr(prompt_commands, "get_valid_users", valid_users)
    monkeypatch.setattr(prompt_commands, "get_user_display_name", display_name)
    monkeypatch.setattr(prompt_commands, "get_chat_participant_activity", participant_activity)
    monkeypatch.setattr(
        prompt_commands,
        "bot",
        SimpleNamespace(get_me=get_me, get_chat_member=get_chat_member),
    )
    monkeypatch.setattr(prompt_commands.random, "sample", lambda values, k: list(values)[:k])
    monkeypatch.setattr(prompt_commands.random, "random", lambda: 0.0)
    monkeypatch.setattr(prompt_commands.random, "choice", lambda values: "Карл")
    monkeypatch.setattr(prompt_commands.random, "shuffle", put_bot_in_middle)

    characters = asyncio.run(prompt_commands._get_dynamic_poem_characters("-1001"))
    hero_list = [part.strip() for part in characters.split(",")]

    assert hero_list == ["Света", "Алина", "Карл", "Детектор", "Ольга", "Никита"]
    assert len(hero_list) == 6
    assert "Упупа" not in hero_list
    assert "Сглыпа" not in hero_list


def test_poem_prompts_do_not_hardcode_legacy_chat_members():
    from prompts import PROMPT_PIROZHOK, PROMPT_PIROZHOK1, PROMPT_POROSHOK, PROMPT_POROSHOK1

    assert PROMPT_PIROZHOK1 is PROMPT_PIROZHOK
    assert PROMPT_POROSHOK1 is PROMPT_POROSHOK
    assert "анна" not in PROMPT_PIROZHOK1[0].lower()
    assert "анна" not in PROMPT_POROSHOK1[0].lower()


def test_poem_bot_detection_ignores_unknown_humans(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from AI.dialog import prompt_commands

    async def activity(*args, **kwargs):
        return [{"user_id": 999, "message_count": 4, "user_name": "Случайный"}]

    async def get_me():
        return SimpleNamespace(first_name="Упупа", full_name="Упупа")

    async def get_chat_member(chat_id, user_id):
        return SimpleNamespace(
            user=SimpleNamespace(is_bot=False, first_name="Случайный", full_name="Случайный")
        )

    monkeypatch.setattr(prompt_commands, "get_chat_participant_activity", activity)
    monkeypatch.setattr(
        prompt_commands,
        "bot",
        SimpleNamespace(get_me=get_me, get_chat_member=get_chat_member),
    )

    names = asyncio.run(prompt_commands._get_active_poem_bot_names("-1001", set()))

    assert names == ["Упупа"]


def test_poem_bot_is_optional_and_has_no_special_prompt_position(monkeypatch):
    from AI.dialog import prompt_commands

    monkeypatch.setattr(prompt_commands.random, "random", lambda: 0.0)
    monkeypatch.setattr(prompt_commands.random, "choice", lambda values: "Сглыпа")
    monkeypatch.setattr(prompt_commands.random, "shuffle", lambda values: values.reverse())

    text = prompt_commands._format_poem_character_instruction(
        ["Упупа", "Карл", "Сглыпа"],
        "Света, Алина, Жека",
    )

    assert text == "Сглыпа, Жека, Алина, Света"
    assert "обязательный" not in text

    monkeypatch.setattr(prompt_commands.random, "random", lambda: 0.9)
    monkeypatch.setattr(prompt_commands.random, "shuffle", lambda values: None)

    text_without_bot = prompt_commands._format_poem_character_instruction(
        ["Упупа", "Карл", "Сглыпа"],
        "Света, Алина, Жека",
    )

    assert text_without_bot == "Света, Алина, Жека"


def test_poem_active_bot_pool_is_chat_specific_and_not_hardcoded(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from AI.dialog import prompt_commands

    async def activity(chat_id, *, period_hours, limit):
        assert chat_id == -2002
        return [
            {"user_id": 501, "message_count": 20, "user_name": "Чатобот"},
            {"user_id": 502, "message_count": 10, "user_name": "Мемобот"},
        ]

    async def get_me():
        return SimpleNamespace(first_name="Упупа", full_name="Упупа")

    async def get_chat_member(chat_id, user_id):
        names = {501: "Чатобот", 502: "Мемобот"}
        return SimpleNamespace(
            user=SimpleNamespace(
                is_bot=True,
                first_name=names[user_id],
                full_name=names[user_id],
            )
        )

    monkeypatch.setattr(prompt_commands, "get_chat_participant_activity", activity)
    monkeypatch.setattr(
        prompt_commands,
        "bot",
        SimpleNamespace(get_me=get_me, get_chat_member=get_chat_member),
    )

    names = asyncio.run(prompt_commands._get_active_poem_bot_names("-2002", set()))

    assert names == ["Упупа", "Чатобот", "Мемобот"]



def test_poem_bot_name_uses_first_word_and_cyrillic():
    from AI.dialog.prompt_commands import _normalize_poem_bot_name

    assert _normalize_poem_bot_name("Upupa Epops") == "Упупа"
    assert _normalize_poem_bot_name("mira") == "Мира"
    assert _normalize_poem_bot_name("Сглыпа Великая") == "Сглыпа"
    assert _normalize_poem_bot_name("🤖 Karl Bot") == "Карл"


def test_poem_user_name_keeps_all_words_removes_emoji_and_uses_cyrillic():
    from AI.dialog.prompt_commands import _normalize_poem_user_name

    assert _normalize_poem_user_name("bagr") == "Багр"
    assert _normalize_poem_user_name("sofiko 🙃") == "Софико"
    assert _normalize_poem_user_name("v v") == "В В"
    assert _normalize_poem_user_name("Anna Maria ✨ Petrova") == "Анна Мариа Петрова"
    assert _normalize_poem_user_name("Арина 🙃") == "Арина"


def test_poem_user_name_aliases_are_applied_only_in_poem_character_lists(monkeypatch):
    from AI.dialog import prompt_commands

    aliases = {
        "Six7ape": "Мухтар",
        "сикс апе": "Мухтар",
        "чудо в стране алис": "Света",
        "мац ня": "Мацоня",
        "алинаааааа": "Алина",
    }
    for raw_name, expected in aliases.items():
        assert prompt_commands._replace_poem_user_name_alias(raw_name) == expected

    assert prompt_commands._replace_poem_user_name_alias("Жека") == "Жека"

    monkeypatch.setattr(prompt_commands.random, "shuffle", lambda values: None)
    characters = prompt_commands._format_poem_character_instruction(
        [],
        "Six7ape, чудо в стране алис, мац ня, алинаааааа, Жека",
    )

    assert characters == "Мухтар, Света, Мацоня, Алина, Жека"


def test_poem_bot_pool_ignores_telegram_fake_channel_senders(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from AI.dialog import prompt_commands

    async def activity(*args, **kwargs):
        return [
            {"user_id": 777000, "message_count": 50, "user_name": "Channel"},
            {"user_id": 1087968824, "message_count": 40, "user_name": "Group"},
            {"user_id": 501, "message_count": 10, "user_name": "mira"},
        ]

    async def get_me():
        return SimpleNamespace(first_name="Upupa Epops", full_name="Upupa Epops")

    seen_member_ids = []

    async def get_chat_member(chat_id, user_id):
        seen_member_ids.append(user_id)
        return SimpleNamespace(
            user=SimpleNamespace(is_bot=True, first_name="mira", full_name="mira")
        )

    monkeypatch.setattr(prompt_commands, "get_chat_participant_activity", activity)
    monkeypatch.setattr(
        prompt_commands,
        "bot",
        SimpleNamespace(get_me=get_me, get_chat_member=get_chat_member),
    )

    names = asyncio.run(prompt_commands._get_active_poem_bot_names("-2002", set()))

    assert names == ["Упупа", "Мира"]
    assert seen_member_ids == [501]



def test_poem_dynamic_human_names_are_normalized_before_prompt(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from AI.dialog import prompt_commands

    async def valid_users(chat_id):
        return {
            "11": {"weekly": 10, "daily": 3, "total": 100},
            "22": {"weekly": 9, "daily": 2, "total": 90},
            "33": {"weekly": 8, "daily": 1, "total": 80},
        }

    names = {
        11: "bagr",
        22: "sofiko 🙃",
        33: "v v",
    }

    async def display_name(chat_id, user_id):
        return names[user_id]

    async def participant_activity(*args, **kwargs):
        return []

    async def get_me():
        return SimpleNamespace(first_name="Upupa Epops", full_name="Upupa Epops")

    monkeypatch.setattr(prompt_commands, "get_valid_users", valid_users)
    monkeypatch.setattr(prompt_commands, "get_user_display_name", display_name)
    monkeypatch.setattr(prompt_commands, "get_chat_participant_activity", participant_activity)
    monkeypatch.setattr(
        prompt_commands,
        "bot",
        SimpleNamespace(get_me=get_me),
    )
    monkeypatch.setattr(prompt_commands.random, "sample", lambda values, k: list(values)[:k])
    monkeypatch.setattr(prompt_commands.random, "random", lambda: 0.9)
    monkeypatch.setattr(prompt_commands.random, "shuffle", lambda values: None)

    characters = asyncio.run(prompt_commands._get_dynamic_poem_characters("-1001"))

    assert "Багр, Софико, В В" in characters
    assert "🙃" not in characters
    assert "bagr" not in characters
    assert "sofiko" not in characters



def test_poem_prompts_prioritize_named_participants_over_filler():
    from prompts import PROMPT_PIROZHOK, PROMPT_POROSHOK

    for prompt in (PROMPT_PIROZHOK[0], PROMPT_POROSHOK[0]):
        lowered = prompt.lower()
        assert "используй как можно больше" in lowered
        assert "порядок списка не задаёт главного героя" in lowered
        assert "не вводи новых случайных или безымянных персонажей" in lowered



def test_poem_format_validator_requires_exactly_four_nonempty_lines():
    from AI.dialog.prompt_commands import _is_valid_poem_response

    assert _is_valid_poem_response("раз\nдва\nтри\nчетыре")
    assert _is_valid_poem_response("раз\n\nдва\nтри\nчетыре\n")
    assert not _is_valid_poem_response("раз два три четыре")
    assert not _is_valid_poem_response("раз\nдва\nтри")
    assert not _is_valid_poem_response("раз\nдва\nтри\nчетыре\nпять")


def test_poem_generation_retries_malformed_output(monkeypatch):
    import asyncio
    from AI.dialog import prompt_commands

    calls = []
    responses = iter([
        "мира ведет владимира в лес где все едят чтобы наесться человечиной",
        "света идет домой\nалина варит суп\nжека режет хлеб\nникита ест носок",
    ])

    async def fake_generate(prompt, chat_id):
        calls.append((prompt, chat_id))
        return next(responses)

    monkeypatch.setattr(prompt_commands, "generate_simple_response", fake_generate)

    result = asyncio.run(
        prompt_commands._generate_valid_poem("базовый промпт", "-1001", "пирожок")
    )

    assert result == "света идет домой\nалина варит суп\nжека режет хлеб\nникита ест носок"
    assert len(calls) == 2
    assert calls[0][0] == "базовый промпт"
    assert "ровно из четырёх" in calls[1][0]
    assert "не склеивай строки в одну" in calls[1][0].lower()


def test_poem_generation_returns_none_after_three_malformed_attempts(monkeypatch):
    import asyncio
    from AI.dialog import prompt_commands

    calls = []

    async def fake_generate(prompt, chat_id):
        calls.append(prompt)
        return "одна длинная строка без четверостишия"

    monkeypatch.setattr(prompt_commands, "generate_simple_response", fake_generate)

    result = asyncio.run(
        prompt_commands._generate_valid_poem("базовый промпт", "-1001", "порошок")
    )

    assert result is None
    assert len(calls) == prompt_commands._POEM_MAX_GENERATION_ATTEMPTS



def test_poem_active_users_ignore_telegram_fake_senders():
    from AI.dialog.prompt_commands import _rank_active_poem_users

    users = {
        "777000": {"weekly": 100, "daily": 100, "total": 1000},
        "1087968824": {"weekly": 90, "daily": 90, "total": 900},
        "42": {"weekly": 5, "daily": 2, "total": 50},
    }

    assert _rank_active_poem_users(users) == ["42"]


def test_valid_users_exclude_persisted_telegram_non_human_senders(monkeypatch):
    import asyncio
    from features import stat_rank_settings

    class FakeCounters:
        def get_chat(self, chat_id, today):
            assert chat_id == "-1001"
            return {
                "777000": {"weekly": 100, "daily": 100, "total": 1000},
                "1087968824": {"weekly": 90, "daily": 90, "total": 900},
                "42": {"weekly": 5, "daily": 2, "total": 50},
            }

    monkeypatch.setattr(stat_rank_settings, "_counter_repository", FakeCounters())

    users = asyncio.run(stat_rank_settings.get_valid_users("-1001"))

    assert users == {
        "42": {"weekly": 5, "daily": 2, "total": 50},
    }


def test_track_message_statistics_ignores_sender_chat(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from features import stat_rank_settings

    class FailingCounters:
        def increment(self, *args, **kwargs):
            raise AssertionError("sender_chat must not be counted as a human")

    monkeypatch.setattr(stat_rank_settings, "_counter_repository", FailingCounters())

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=777000, is_bot=False),
        sender_chat=SimpleNamespace(id=-100123),
        chat=SimpleNamespace(id=-1001),
    )

    asyncio.run(stat_rank_settings.track_message_statistics(message))
