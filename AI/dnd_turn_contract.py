"""Small, non-truncatable mechanics contract sent to every story provider.

The full system prompt is larger than the provider budgets. Critical action and
metadata schemas must not depend on which middle section happens to be clipped.
"""
from __future__ import annotations

import re


TURN_CONTRACT = """ОБЯЗАТЕЛЬНЫЙ ПРОТОКОЛ DND (ID бери из участников, примеры не факты мира):
До 100 слов сцены, затем скрытые метаданные, последним один ACTION.
Свободный личный ход [ACTION:INPUT;TARGETS:id], общий [ACTION:INPUT].
Общий выбор [ACTION:POLL;OPTIONS:путь 1;путь 2;путь 3]. Планируй хотя бы одну общую
развилку за сюжет и затем иногда повторяй; варианты не обязаны исчерпывать все идеи:
кнопка «Свой вариант» добавляется кодом. Не заменяй голосованием заявленное действие.
Проверка только для возможного, неопределённого действия с ценой провала:
[ACTION:ROLL;TYPE:CHECK;ABILITY:DEX;SKILL:Ловкость рук;REASON:открыть заевший замок;DC:12;TARGETS:id].
Для реакции на опасность TYPE:SAVE. ABILITY выбирай по действию, DC 6–17.
REASON всегда конкретен, «проверка по ситуации» запрещена. TARGETS — исполнитель
ещё не разрешённого действия, не следующий по очереди. Пока ждём этот бросок,
не задавай вопрос другому герою. Текст, REASON и TARGETS относятся к одному действию.
Прямая атака героя ТОЛЬКО [ACTION:PLAYER_ATTACK;TARGETS:id;ENEMY:имя;HP:16;AC:12;POWER:MEDIUM;WEAPON:меч;STYLE:MELEE;MODE:NORMAL;REASON:удар по врагу].
Атака NPC [ACTION:ENEMY_ATTACK;TARGETS:id;ENEMY:имя;HP:16;AC:12;POWER:MEDIUM;REASON:атака врага].
STYLE:MELEE/RANGED/SPECIAL. Попадание d20 + модификатор + владение против КБ,
затем кубики урона + модификатор считает код. Не описывай исход до бросков.
HP/AC обязательны, имена врагов стабильны, сохранённые HP не сбрасывай.
За полный сюжет запланируй 1–2 настоящие схватки с NPC: первую после знакомства
и первого круга действий, вторую ближе к развязке. Не заменяй бой погоней или CHECK.
Не атакуй от имени героя без его заявки. Между схватками дай мирные сцены.
Уважай успешный обход боя, сдачу и досрочное завершение игроками.
Дроны и кибер-монстры не враги по умолчанию: выбирай противников из темы сюжета,
с разными мотивами и тактикой; не вводи технику в мир без сюжетного основания.
Фактическое получение вещи ОБЯЗАТЕЛЬНО [ITEM:ADD;PLAYER:id;NAME:название;QTY:1;KIND:item].
Потеря/расход [ITEM:REMOVE;PLAYER:id;NAME:название]. Нерешённая попытка не даёт вещь.
Каждого встреченного сюжетного NPC сохраняй [NPC:имя;EVENT:взаимодействие;NOTE:отношение и факт].
Это память знакомых и врагов, а не отношения между игроками. Не выдумывай знакомства.
""".strip()


def turn_contract(session) -> str:
    if getattr(session, "mode", None) != "participants":
        return ""
    scene = int(getattr(session, "scene_count", 0) or 0)
    enemies = getattr(session, "enemy_combatants", {}) or {}
    decisions = int(getattr(session, "spotlight_decisions_since_poll", 0) or 0)
    notes = [f"Сцена {scene}; зарегистрировано противников: {len(enemies)}."]
    if scene >= 3 and not enemies:
        notes.append("Первой схватки ещё не было: подведи к сюжетному противнику в ближайшем эпизоде и дай героям действовать.")
    if decisions >= 4:
        notes.append("Давно не было голосования: следующую общую сюжетную развилку оформи POLL с 2–3 конкретными путями.")
    return TURN_CONTRACT + "\n" + " ".join(notes)


_ROLL = re.compile(r"\[ACTION:ROLL(?:;([^\]]*))?\]", re.I)


def roll_needs_repair(response: str, session=None) -> bool:
    match = _ROLL.search(response)
    if not match:
        return False
    fields = dict(part.split(":", 1) for part in (match[1] or "").split(";") if ":" in part)
    fields = {key.strip().upper(): value.strip() for key, value in fields.items()}
    if (not fields.get("REASON") or "проверка по ситуации" in fields["REASON"].casefold()
            or not fields.get("TARGETS") or fields.get("ABILITY") not in {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
            or not fields.get("DC", "").isdigit()):
        return True
    ids = {token.strip() for token in fields["TARGETS"].split(",")}
    if any(not uid.isdigit() for uid in ids):
        return True
    if session is not None:
        participants = getattr(session, "participants", {}) or {}
        if any(uid not in participants or not participants[uid].get("active", True) for uid in ids):
            return True
        for key, player in participants.items():
            name = str(player.get("name") or "")
            if (str(key) not in ids and name and
                    re.match(re.escape(name) + r"\s", fields["REASON"], re.I)):
                return True
    return False


def align_roll_question(session, response: str) -> str:
    """Drop a premature question to another hero while a roll is outstanding."""
    match = _ROLL.search(response)
    if not match:
        return response
    targets = re.search(r"(?:^|;)TARGETS:([\d, ]+)", match[1] or "", re.I)
    if not targets:
        return response
    ids = {token.strip() for token in targets[1].split(",")}
    other_names = [str(player.get("name") or "") for key, player in
                   (getattr(session, "participants", {}) or {}).items() if str(key) not in ids]
    # Restrict removal to question sentences mentioning an off-turn hero;
    # retain descriptions, consequences and metadata verbatim.
    body = response[:match.start()]
    sentences = re.split(r"(?<=[.!?])\s+", body)
    body = "\n".join(sentence for sentence in sentences if not (
        "?" in sentence and any(name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", sentence, re.I)
                                for name in other_names)))
    return body.rstrip() + "\n" + response[match.start():]


def install_turn_contract_guard(dnd) -> None:
    if getattr(dnd, "_turn_contract_guard_installed", False):
        return
    original = dnd.generate_session_response

    async def generate(session, prompt):
        response = await original(session, prompt)
        if getattr(session, "mode", None) != "participants":
            return response
        if roll_needs_repair(response, session):
            from AI.dnd_generation_resilience import generate_auxiliary_text

            roster = ", ".join(f"{key}: {row.get('name')}" for key, row in session.participants.items())
            repaired = await generate_auxiliary_text(
                session,
                "Исправь только ACTION:ROLL для уже заявленного действия. Верни один тег с TYPE, "
                "ABILITY, конкретным REASON, DC и TARGETS реального исполнителя из списка. "
                "Не назначай следующего героя по очереди. Если действие/исполнитель неясны, верни NONE.\n"
                f"Участники: {roster}\nТекущая заявка: {str(prompt)[-3500:]}\nОтвет: {response}",
                allow_groq_fallback=True,
            )
            candidate = _ROLL.search(repaired or "")
            valid = candidate and not roll_needs_repair(candidate[0], session)
            if valid:
                response = _ROLL.sub(lambda _: candidate[0], response, count=1)
            else:
                # An unspecified check must never become an arbitrary WIS roll
                # assigned to whoever comes next in the spotlight queue.
                match = _ROLL.search(response)
                targets = re.search(r";TARGETS:([\d, ]+)", match[0], re.I)
                ids = [uid.strip() for uid in targets[1].split(",")] if targets else []
                ids = [uid for uid in ids if uid in session.participants]
                target = ";TARGETS:" + ",".join(ids) if ids else ""
                response = _ROLL.sub(
                    "Уточни, что именно и каким способом пытаешься сделать.\n[ACTION:INPUT" + target + "]",
                    response, count=1,
                )
        return align_roll_question(session, response)

    dnd.generate_session_response = generate
    dnd._turn_contract_guard_installed = True
