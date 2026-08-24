"""Apply the chat's current persona prompt to the final `чотам` result."""

import logging

from AI.dialog.settings import build_prompt_with_current_chat_prompt
from AI.whoparody import generate_with_active_model


async def apply_current_prompt_to_whatisthere(raw_response: str, chat_id: int) -> str:
    """Restyle a successful `чотам` analysis using the chat's current prompt.

    The media/text analyzer remains responsible for extracting facts and following any
    explicit instruction written after `чотам`. This second text-only pass changes only
    presentation/persona, matching the approach already used by `чобыло` and `кто я`.
    If the styling pass fails or returns an empty answer, preserve the original analysis.
    """
    raw_response = (raw_response or "").strip()
    if not raw_response:
        return raw_response

    task_prompt = f"""
Ниже уже готовый результат команды «чотам». Переформулируй его как финальный ответ пользователю.

Жёсткие правила:
- Сохрани факты, смысл и выводы исходного результата.
- Не проводи новый анализ и не выдумывай деталей, которых нет в исходном результате.
- Не упоминай, что ты что-то «переформулируешь», не объясняй инструкции и не цитируй их.
- Стиль, тон, лексика, характер и отношение к обсценной лексике должны полностью соответствовать текущему промпту чата.
- Если исходный результат уже короткий, не раздувай его без необходимости.
- Не более 80 слов.

Исходный результат «чотам»:
---
{raw_response}
---

Финальный ответ:
""".strip()

    styled_prompt = build_prompt_with_current_chat_prompt(
        str(chat_id),
        task_prompt,
        task_name="финальный ответ команды «чотам»",
    )

    try:
        styled = await generate_with_active_model(styled_prompt, chat_id)
        styled = (styled or "").strip()
        if styled:
            logging.info("чотам: финальный ответ успешно пропущен через текущий промпт")
            return styled
        logging.warning("чотам: current-prompt pass вернул пустой ответ, оставляем исходный")
    except Exception as exc:
        logging.error(
            "чотам: не удалось применить текущий промпт, оставляем исходный ответ: %s",
            exc,
            exc_info=True,
        )

    return raw_response
