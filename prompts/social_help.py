"""Дополнение интерактивной справки командами Летописи и отношений."""

from __future__ import annotations


SOCIAL_MEMORY_HELP_BLOCK = (
    "<b>📜 ЛЕТОПИСЬ И ОТНОШЕНИЯ</b>\n"
    "<code>летопись</code> / <code>летопись @user</code> - важные события всего чята или одного человека. Можно реплаем.\n"
    "<code>зал славы</code> - главные события за всё время\n"
    "<code>отношения @user</code> / <code>история @user</code> - как вы общаетесь сейчас и как менялась ваша связь. "
    "Обе команды можно писать реплаем или с двумя никами (@user1 @user2) для чужой пары.\n"
    "<code>мои отношения</code> / <code>отношения чата</code> - твои главные связи и самые заметные пары чята\n\n"
    "<i>Близость — насколько активно и взаимно пара общается в последние недели. "
    "Напряжение — стычки именно между этими двумя (по Летописи) и ядовитые реакции друг на друга, мат не считается. "
    "Уровень 0–100 — место пары среди всех пар чата, #N — её позиция в рейтинге.</i>"
)


def install_social_memory_help(help_texts_module) -> None:
    """Добавить пользовательские команды поверх соцграфа и Летописи."""
    section = help_texts_module.HELP_DICT.get("social", "")
    if (
        "<code>летопись</code>" in section
        and "<code>отношения чата</code>" in section
        and "<code>история @user</code>" in section
    ):
        return

    section = section.rstrip() + "\n\n" + SOCIAL_MEMORY_HELP_BLOCK
    help_texts_module.HELP_DICT["social"] = section
    help_texts_module.HELP_TEXT = "\n\n".join(help_texts_module.HELP_DICT.values())
