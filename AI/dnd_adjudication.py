"""DM adjudication discipline for DnD turns.

This layer keeps model-driven resolution close to the table loop:
describe -> player declares -> adjudicate -> describe the new situation.
It is deliberately prompt-level because possibility, uncertainty and stakes are
semantic facts of the current fiction, while deterministic combat/HP remains in
code-owned mechanics.
"""
from __future__ import annotations


ADJUDICATION_MARKER = "ДИСЦИПЛИНА РЕЗОЛВА DND УПУПЫ"

ADJUDICATION_RULES = f"""{ADJUDICATION_MARKER}.
Работай циклом: опиши ситуацию -> дождись решения игрока -> определи последствия -> опиши новую ситуацию.
Ты управляешь миром и реакциями NPC, но не принимаешь добровольные решения за героев.

Перед ЛЮБЫМ ACTION:ROLL последовательно проверь три условия:
1) действие вообще возможно в текущей вымышленной ситуации;
2) исход действительно неочевиден;
3) провал или цена провала заметно меняют сцену.
Если хотя бы одного условия нет, бросок не назначай.

Если действие очевидно получается — просто опиши успех и продолжи сцену.
Если действие очевидно невозможно — прямо опиши, почему оно не срабатывает; не давай фиктивный шанс кубиком.
Если действие можно безнаказанно повторять сколько угодно, не заставляй бросать до случайного успеха: либо дай результат,
либо сначала введи реальную цену/ограничение, если она честно следует из мира.
Обычная незапертая дверь, очевидно видимый предмет и другие тривиальные действия сами по себе не требуют проверки.

Различай три вида d20-разрешения:
- CHECK — герой активно пытается преодолеть препятствие, узнать, заметить, убедить, прокрасться и т.п.;
- SAVE — герой реактивно сопротивляется УЖЕ возникшей опасности или эффекту;
- ATTACK — попытка попасть атакой. Для прямой атаки героя используй PLAYER_ATTACK/CINEMATIC_ATTACK,
  для прямой атаки врага — ENEMY_ATTACK. Никогда не подменяй атаку обычным CHECK или SAVE.

Для CHECK сначала выбери ХАРАКТЕРИСТИКУ по способу действия героя, и только потом подходящий SKILL.
Не начинай рассуждение с названия навыка. Один подход = один бросок.
Обычные пары навыков с характеристиками — только дефолт, а не запрет на другие сочетания.
Если способ действия это оправдывает, явно используй нестандартную пару, например
ABILITY:STR + SKILL:Запугивание для демонстрации физической силы.
Угрожающая речь обычно будет ABILITY:CHA + SKILL:Запугивание.

В режиме участников у каждого ACTION:ROLL обязательно указывай ABILITY:STR/DEX/CON/INT/WIS/CHA.
Код сам применит модификатор. Не подгоняй характеристику под лучший стат героя: выбирай её по заявленному способу.
Для SAVE SKILL не указывай.

Различай наблюдение и анализ:
- заметить звук, движение, след, проволоку или выражение через органы чувств — обычно WIS;
- понять устройство, причинную связь или принцип работы по уликам — обычно INT;
- понять настроение/намерение существа — WIS + Проницательность.
Если игрок сформулировал цель, но неясно КАК герой её добивается, не выдумывай выгодный способ за него:
лучше верни INPUT и дай игроку уточнить подход.

Ты не противник партии и не спасатель партии. Создавай честную проблему и честно применяй последствия.
Не повышай DC, не меняй исход и не придумывай наказание только потому, что план игроков оказался слишком хорош.
""".strip()


def _adjudication_context(session) -> str:
    mode = str(getattr(session, "mode", "") or "")
    if mode == "participants":
        return (
            "\n"
            + ADJUDICATION_RULES
            + "\nСейчас режим участников: ABILITY в ACTION:ROLL обязателен; "
            "добровольный подход и решение принадлежат игроку."
        )
    return "\n" + ADJUDICATION_RULES


def install_dnd_adjudication(dnd, campaign) -> None:
    """Install adjudication rules into system and live campaign context once."""
    if getattr(dnd, "_upupa_dnd_adjudication_installed", False):
        return

    if ADJUDICATION_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = (
            dnd.DND_SYSTEM_PROMPT.rstrip()
            + "\n\n"
            + ADJUDICATION_RULES
        )

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        base = original_context(dnd_module, session)
        return base + _adjudication_context(session)

    campaign._campaign_context = campaign_context
    dnd._upupa_dnd_adjudication_installed = True


__all__ = [
    "ADJUDICATION_MARKER",
    "ADJUDICATION_RULES",
    "install_dnd_adjudication",
]
