"""Дополнение интерактивной справки командами D&D."""

from __future__ import annotations


DND_HELP_BLOCK = (
    "<b>🧙‍♂️ ДНД</b>\n"
    "<code>упупа днд</code> / <code>упупа начни историю</code> - создать новую игру и выбрать режим\n"
    "<code>днд</code> - снова показать открытое лобби с текущими участниками\n"
    "<code>днд старт</code> - ведущему перейти к выбору сюжета\n"
    "<code>герой</code> / <code>днд герой</code> - твой профиль, репутация и инвентарь\n"
    "<code>инвентарь</code> / <code>днд инвентарь</code> - твои реальные вещи и артефакты\n"
    "<code>передать [предмет]</code> / <code>отдать [предмет]</code> (реплаем) - передать вещь или артефакт другому игроку; количество можно указать числом\n"
    "<code>днд связи</code> - важные сюжетные NPC и что связывает их с партией\n"
    "<code>днд сюжет</code> - текущий сюжет, сцена, угроза и что сейчас ждём от игроков\n"
    "<code>враги</code> / <code>днд враги</code> - во время боя показать оставшихся противников, их HP и КБ\n"
    "<code>днд конец</code> - закончить сюжет\n"
    "Герой, инвентарь, связи и сюжет доступны и между партиями: тогда показывается последнее сохранённое состояние. "
    "Команда врагов работает только по текущему бою."
)

_PREVIOUS_DND_HELP_BLOCK = (
    "<b>🧙‍♂️ ДНД</b>\n"
    "<code>упупа днд</code> / <code>упупа начни историю</code> - начать ебучий сюжет в днд\n"
    "<code>мой герой</code> - твой текущий или последний сохранённый профиль и репутация\n"
    "<code>инвентарь</code> - твои реальные вещи и артефакты\n"
    "<code>наши знакомые</code> - кого партия успела встретить и что про них помнит\n"
    "<code>что происходит?</code> - текущий сюжет, сцена, угроза и что сейчас ждём от игроков\n"
    "<code>упупа заверши историю</code> / <code>упупа закончи историю</code> - закончить сюжет\n"
    "Команды состояния работают и между партиями: тогда показывается последнее сохранённое состояние."
)

_OLD_SHORT_BLOCK = (
    "<b>🧙‍♂️ ДНД</b>\n"
    "<code>упупа начни историю</code> - начать ебучий сюжет в днд\n"
    "<code>упупа заверши историю</code> / <code>упупа закончи историю</code> - закончить сюжет"
)


def install_dnd_help(help_texts_module) -> None:
    """Заменить старый D&D-блок на актуальный список команд."""
    section = help_texts_module.HELP_DICT.get("creative", "")
    if (
        "<code>днд сюжет</code>" in section
        and "<code>днд враги</code>" in section
        and "<code>днд конец</code>" in section
        and "<code>передать [предмет]</code>" in section
    ):
        return

    if _PREVIOUS_DND_HELP_BLOCK in section:
        section = section.replace(_PREVIOUS_DND_HELP_BLOCK, DND_HELP_BLOCK, 1)
    elif _OLD_SHORT_BLOCK in section:
        section = section.replace(_OLD_SHORT_BLOCK, DND_HELP_BLOCK, 1)
    else:
        section = section.rstrip() + "\n\n" + DND_HELP_BLOCK

    help_texts_module.HELP_DICT["creative"] = section
    help_texts_module.HELP_TEXT = "\n\n".join(help_texts_module.HELP_DICT.values())
