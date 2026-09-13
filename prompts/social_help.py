"""Дополнение интерактивной справки командами Летописи и отношений."""

from __future__ import annotations


SOCIAL_MEMORY_HELP_BLOCK = (
    "<b>📜 ЛЕТОПИСЬ И ОТНОШЕНИЯ</b>\n"
    "<code>летопись</code> - важные события чята, которые Упупа сохранил в историю\n"
    "<code>летопись @user</code> / <code>летопись</code> реплаем - события с участием конкретного гражданина\n"
    "<code>отношения @user</code> / <code>отношения</code> реплаем - текущее состояние ваших отношений\n"
    "<code>отношения @user1 @user2</code> - текущее состояние указанной пары\n"
    "<code>мои отношения</code> - твои главные накопленные связи\n"
    "<code>отношения чата</code> - самые заметные пары и перекосы всего чята\n"
    "<code>история @user</code> / <code>история</code> реплаем - история вашей пары по уже накопленным данным\n"
    "<code>история @user1 @user2</code> - история указанной пары\n"
    "<code>история отношений ...</code> - совместимый алиас команды <code>история</code>"
)


def install_social_memory_help(help_texts_module) -> None:
    """Добавить пользовательские команды поверх соцграфа и Летописи."""
    section = help_texts_module.HELP_DICT.get("social", "")
    if (
        "<code>летопись</code>" in section
        and "<code>отношения чата</code>" in section
        and "<code>история @user1 @user2</code>" in section
    ):
        return

    section = section.rstrip() + "\n\n" + SOCIAL_MEMORY_HELP_BLOCK
    help_texts_module.HELP_DICT["social"] = section
    help_texts_module.HELP_TEXT = "\n\n".join(help_texts_module.HELP_DICT.values())
