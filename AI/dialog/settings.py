"""Chat-level dialogue settings and prompt composition."""

from config import chat_settings
from prompts import PROMPTS_DICT


NO_CONFIDENCE_PERCENTAGES_INSTRUCTION = (
    "Не упоминай процент уверенности, оценку уверенности или численную вероятность своей правоты. "
    "Если данных не хватает, коротко скажи, что именно нужно уточнить, без процентов."
)


def update_chat_settings(chat_id: str) -> None:
    """Initialize dialogue settings for a new chat, preserving legacy defaults."""
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True,
            "reactions_enabled": True,
            "prompt": PROMPTS_DICT.get("врач", ""),
            "prompt_name": "летописец",
            "prompt_source": "daily",
            "active_model": "gemini",
        }


def get_current_chat_prompt(chat_id: str) -> tuple[str, str]:
    update_chat_settings(chat_id)
    settings = chat_settings.get(chat_id, {})
    prompt_text = settings.get("prompt", PROMPTS_DICT.get("летописец", ""))
    prompt_name = settings.get("prompt_name", "летописец")
    return prompt_text, prompt_name


def build_prompt_with_current_chat_prompt(
    chat_id: str,
    task_prompt: str,
    task_name: str = "служебную команду",
) -> str:
    """Add the current chat persona to a standalone generation task."""
    selected_prompt, prompt_name = get_current_chat_prompt(str(chat_id))
    return (
        f"{selected_prompt}\n\n"
        f"{NO_CONFIDENCE_PERCENTAGES_INSTRUCTION}\n"
        f"Ты выполняешь {task_name} в этом чате от лица '{prompt_name}'. "
        f"Сохрани характер, тон, лексику и манеру текущего промпта, но строго выполни задачу ниже. "
        f"Не превращай ответ в обычный диалог и не объясняй эти инструкции. "
        f"Ответ должен быть непустым текстом на русском языке.\n\n"
        f"Задача:\n{task_prompt}"
    )
