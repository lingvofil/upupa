"""«случайный промпт»: бот сам придумывает персонажа, «расскажи промпт» его показывает."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests import test_smoke_imports  # noqa: F401  (env + моки)

from AI.dialog import prompt_commands
from prompts.personas import PROMPTS_DICT

CHAT_ID = "-1001"
GOOD_PROMPT = (
    "ты ночной сторож на заводе, ворчишь на всех, подозреваешь несунов, "
    "вспоминаешь смену 1987 года, не более 70 слов"
)
GOOD_RESPONSE = f"ИМЯ: сторож\nПРОМПТ: {GOOD_PROMPT}"


class MessageStub:
    def __init__(self, text=""):
        self.text = text
        self.chat = SimpleNamespace(id=int(CHAT_ID))
        self.replies = []

    async def reply(self, text, **kwargs):
        self.replies.append(text)


@pytest.fixture
def chat(monkeypatch):
    """Настройки одного чата; модель подменяется очередью готовых ответов."""
    settings = {
        "prompt": "старый промпт",
        "prompt_name": "бабка",
        "prompt_type": "standard",
        "active_model": "gemini",
    }
    monkeypatch.setitem(prompt_commands.chat_settings, CHAT_ID, settings)
    monkeypatch.setattr(prompt_commands, "save_chat_settings", lambda: None)

    async def send_chat_action(**kwargs):
        return None

    monkeypatch.setattr(
        prompt_commands,
        "bot",
        SimpleNamespace(send_chat_action=send_chat_action),
    )

    state = SimpleNamespace(settings=settings, responses=[], prompts=[])

    async def generate(prompt, chat_id):
        assert chat_id == CHAT_ID
        state.prompts.append(prompt)
        return state.responses.pop(0)

    monkeypatch.setattr(prompt_commands, "generate_simple_response", generate)
    return state


def _generate(state, *responses):
    state.responses.extend(responses)
    message = MessageStub("случайный промпт")
    asyncio.run(prompt_commands.handle_generate_random_prompt_command(message))
    return message


def _tell():
    message = MessageStub("расскажи промпт")
    asyncio.run(prompt_commands.handle_tell_generated_prompt_command(message))
    return message


def test_parse_accepts_labelled_answer_and_tolerates_markdown():
    assert prompt_commands._parse_random_persona(GOOD_RESPONSE) == ("сторож", GOOD_PROMPT)
    assert prompt_commands._parse_random_persona(
        f"Вот персонаж:\n\n**ИМЯ:** Ночной Сторож\n\n**Промпт:** «{GOOD_PROMPT}»\n"
    ) == ("ночной сторож", GOOD_PROMPT)
    assert prompt_commands._parse_random_persona(
        f"```\nИМЯ: сторож\nПРОМПТ: {GOOD_PROMPT}\n```"
    ) == ("сторож", GOOD_PROMPT)


def test_parse_drops_model_chatter_after_prompt():
    assert prompt_commands._parse_random_persona(
        f"{GOOD_RESPONSE}\n\nНадеюсь, такой персонаж подойдёт!"
    ) == ("сторож", GOOD_PROMPT)


@pytest.mark.parametrize(
    "response",
    [
        None,
        "",
        "ошибка блят",
        "Я пока не знаю, что ответить... 😅",
        f"ПРОМПТ: {GOOD_PROMPT}",
        "ИМЯ: сторож\nПРОМПТ: коротко",
        f"ИМЯ: сторож\nПРОМПТ: {'очень длинно ' * 200}",
        f"ИМЯ: 🤖🤖🤖\nПРОМПТ: {GOOD_PROMPT}",
        f"ИМЯ: очень длинное название из целых пяти слов\nПРОМПТ: {GOOD_PROMPT}",
    ],
)
def test_parse_rejects_unusable_answers(response):
    assert prompt_commands._parse_random_persona(response) is None


def test_parse_keeps_name_safe_for_settings_markdown():
    name, _ = prompt_commands._parse_random_persona(
        f"ИМЯ: `сантехник_мистик*`\nПРОМПТ: {GOOD_PROMPT}"
    )

    assert not set(name) & set("`*_[]")


def test_parse_adds_length_limit_only_when_missing():
    without_limit = "ты сторож на заводе, используешь устаревшие словечки и ворчишь."

    _, prompt_text = prompt_commands._parse_random_persona(
        f"ИМЯ: сторож\nПРОМПТ: {without_limit}"
    )
    assert prompt_text == (
        "ты сторож на заводе, используешь устаревшие словечки и ворчишь, не более 70 слов"
    )

    _, prompt_text = prompt_commands._parse_random_persona(GOOD_RESPONSE)
    assert prompt_text == GOOD_PROMPT


def test_generation_prompt_has_examples_seed_and_taken_names(monkeypatch):
    monkeypatch.setattr(prompt_commands.random, "choice", lambda values: values[0])

    prompt = prompt_commands._build_random_persona_prompt({"бабка", "сторож"})

    assert PROMPTS_DICT["гвардеец"] in prompt
    assert prompt_commands.RANDOM_PERSONA_ROLES[0] in prompt
    assert prompt_commands.RANDOM_PERSONA_QUIRKS[0] in prompt
    assert prompt_commands.RANDOM_PERSONA_SPEECH[0] in prompt
    assert "бабка, сторож" in prompt


def test_random_prompt_becomes_chat_persona_without_revealing_it(chat):
    chat.settings.update(
        prompt_type="user_style",
        imitated_user={"display_name": "Вася"},
        style_turn_samples=["привет"],
    )

    message = _generate(chat, GOOD_RESPONSE)

    assert chat.settings["prompt"] == GOOD_PROMPT
    assert chat.settings["prompt_name"] == "сторож"
    assert chat.settings["prompt_type"] == "generated"
    assert chat.settings["prompt_source"] == "user"
    assert "imitated_user" not in chat.settings
    assert "style_turn_samples" not in chat.settings

    assert message.replies == ["Придумал себе персонажа нахуй!"]


def test_tell_prompt_shows_what_was_invented(chat):
    _generate(chat, GOOD_RESPONSE)

    reply = _tell().replies[0]

    assert "сторож" in reply
    assert GOOD_PROMPT in reply


def test_current_prompt_command_names_generated_persona(chat):
    _generate(chat, GOOD_RESPONSE)

    message = MessageStub("какой промпт")
    asyncio.run(prompt_commands.handle_current_prompt_command(message))

    assert message.replies == ["Я сторож."]


def test_generation_retries_bad_answers_and_rerolls_seed(chat):
    _generate(chat, "ошибка блят", "Не могу придумать такого персонажа.", GOOD_RESPONSE)

    assert chat.settings["prompt_name"] == "сторож"
    assert len(chat.prompts) == 3


def test_generation_rejects_taken_names(chat):
    _generate(
        chat,
        f"ИМЯ: Гвардеец\nПРОМПТ: {GOOD_PROMPT}",
        f"ИМЯ: бабка\nПРОМПТ: {GOOD_PROMPT}",
        GOOD_RESPONSE,
    )

    assert chat.settings["prompt_name"] == "сторож"
    assert "гвардеец" in chat.prompts[0]


def test_failed_generation_keeps_current_prompt(chat):
    before = dict(chat.settings)

    message = _generate(chat, "ошибка блят", "ошибка блят", "ошибка блят")

    assert chat.settings == before
    assert len(chat.prompts) == 3
    assert len(message.replies) == 1
    assert "остаюсь" in message.replies[0]


@pytest.mark.parametrize("prompt_type", ["standard", "custom", "user_style", None])
def test_tell_prompt_never_leaks_other_prompts(chat, prompt_type):
    secret = "секретный текст чужого промпта"
    chat.settings.update(prompt=secret, prompt_name="кастомный", prompt_type=prompt_type)

    reply = _tell().replies[0]

    assert secret not in reply
    assert "случайный промпт" in reply


def test_switching_prompt_afterwards_hides_generated_one(chat):
    _generate(chat, GOOD_RESPONSE)

    message = MessageStub("промпт бабка")
    asyncio.run(prompt_commands.handle_set_prompt_command(message))

    reply = _tell().replies[0]
    assert GOOD_PROMPT not in reply
    assert PROMPTS_DICT["бабка"] not in reply


def test_settings_menu_resets_generated_prompt_type(monkeypatch):
    import features.interactive_settings as interactive_settings

    settings = {"prompt": GOOD_PROMPT, "prompt_name": "сторож", "prompt_type": "generated"}
    monkeypatch.setattr(interactive_settings, "chat_settings", {CHAT_ID: settings})
    monkeypatch.setattr(interactive_settings, "save_chat_settings", lambda: None)

    async def allow_settings(*args, **kwargs):
        return True

    async def main_markup(chat_id):
        return "настройки", None

    monkeypatch.setattr(interactive_settings, "has_settings_permission", allow_settings)
    monkeypatch.setattr(interactive_settings, "get_main_settings_markup", main_markup)

    class QueryMessageStub:
        chat = SimpleNamespace(id=int(CHAT_ID))

        async def edit_text(self, *args, **kwargs):
            return None

    class QueryStub:
        message = QueryMessageStub()
        from_user = SimpleNamespace(id=1)
        data = "settings:set_prompt:бабка"

        async def answer(self, *args, **kwargs):
            return None

    asyncio.run(interactive_settings.handle_settings_callback(QueryStub()))

    assert settings["prompt"] == PROMPTS_DICT["бабка"]
    assert settings["prompt_type"] == "standard"


@pytest.mark.parametrize(
    ("text", "callback_name"),
    [
        ("случайный промпт", "generate_random_prompt_command"),
        ("Случайный промпт", "generate_random_prompt_command"),
        ("расскажи промпт", "tell_generated_prompt_command"),
        ("Расскажи промпт", "tell_generated_prompt_command"),
    ],
)
def test_commands_reach_their_handlers_before_any_other_router(text, callback_name):
    from aiogram import types
    from handlers import ROUTERS

    message = types.Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=types.Chat(id=int(CHAT_ID), type="supergroup"),
        from_user=types.User(id=1, is_bot=False, first_name="Тест"),
        text=text,
    )

    async def first_matching_handler():
        for router in ROUTERS:
            for handler in router.message.handlers:
                matched, _ = await handler.check(message, bot=SimpleNamespace())
                if matched:
                    return handler.callback.__name__
        return None

    assert asyncio.run(first_matching_handler()) == callback_name
