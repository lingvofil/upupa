from AI.dialog.settings import build_prompt_with_current_chat_prompt
from core.state import chat_settings
from prompts.personas import PROMPTS_DICT


def test_feature_contract_overrides_persona_format_limits(monkeypatch):
    chat_id = "-1001"
    monkeypatch.setitem(
        chat_settings,
        chat_id,
        {
            "prompt": PROMPTS_DICT["аристократ"],
            "prompt_name": "аристократ",
            "prompt_type": "standard",
        },
    )

    task = "Сделай сводку не более 200 слов, 2-4 коротких абзаца."
    prompt = build_prompt_with_current_chat_prompt(
        chat_id,
        task,
        task_name="суммаризацию сообщений",
    )

    persona_limit = prompt.index("Не более 10 слов")
    priority_rule = prompt.index("Если они конфликтуют")
    task_block = prompt.index("Задача:")

    assert persona_limit < priority_rule < task_block
    assert "общей длине ответа" in prompt
    assert "количеству абзацев/строк" in prompt
    assert task in prompt[task_block:]


def test_aristocrat_persona_does_not_invite_literal_underlining():
    prompt = PROMPTS_DICT["аристократ"].casefold()

    assert "подчеркнуто" not in prompt
    assert "подчёркнуто" not in prompt
    assert prompt.startswith("отвечай всегда нарочито")
