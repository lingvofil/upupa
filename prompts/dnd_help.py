"""Дополнение интерактивной справки командами D&D."""

from __future__ import annotations


DND_HELP_BLOCK = (
    "<b>🧙‍♂️ ДНД</b>\n"
    "<code>упупа днд</code> / <code>упупа начни историю</code> - начать ебучий сюжет в днд\n"
    "<code>мой герой</code> - твой текущий или последний сохранённый профиль и репутация\n"
    "<code>инвентарь</code> - твои реальные вещи и артефакты\n"
    "<code>наши знакомые</code> - кого партия успела встретить и что про них помнит\n"
    "<code>что происходит?</code> - текущий сюжет, сцена, угроза и что сейчас ждём от игроков\n"
    "<code>упупа заверши историю</code> / <code>упупа закончи историю</code> - закончить сюжет\n"
    "Команды состояния работают и между партиями: тогда показывается последнее сохранённое состояние."
)


def install_dnd_help(help_texts_module) -> None:
    """Заменить старый короткий D&D-блок на актуальный список команд."""
    section = help_texts_module.HELP_DICT.get("creative", "")
    if "<code>мой герой</code>" in section and "<code>упупа днд</code>" in section:
        return

    old_block = (
        "<b>🧙‍♂️ ДНД</b>\n"
        "<code>упупа начни историю</code> - начать ебучий сюжет в днд\n"
        "<code>упупа заверши историю</code> / <code>упупа закончи историю</code> - закончить сюжет"
    )
    if old_block in section:
        section = section.replace(old_block, DND_HELP_BLOCK, 1)
    else:
        section = section.rstrip() + "\n\n" + DND_HELP_BLOCK

    help_texts_module.HELP_DICT["creative"] = section
    help_texts_module.HELP_TEXT = "\n\n".join(help_texts_module.HELP_DICT.values())
