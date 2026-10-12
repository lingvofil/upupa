"""Side quests and the party treasury as structured state.

Quests used to exist only as prose (and as NPC "UNRESOLVED" notes), so nothing
could list them, reward them or close them reliably. Now the master declares
them with tags, the code validates and stores them, Memory v2 shows the open
ones back to the model every turn, «днд квесты» shows them to players, a
completed quest pays XP through the progression accounting of the same
response, and the final report lists what was done and what stayed open.

Tags (taught by the optional ``quests`` prompt section):
  [QUEST:ADD;ID:id;TITLE:название;GOAL:что сделать;FROM:кто дал]
  [QUEST:STEP;ID:id;TEXT:что изменилось]
  [QUEST:DONE;ID:id;EVIDENCE:факт]   [QUEST:FAIL;ID:id;REASON:причина]
  [GOLD:DELTA;AMOUNT:+30;CAUSE:причина]

The treasury belongs to the chat's party, not to one adventure: it is
archived with the campaign and starts the next one where the last one ended.
"""
from __future__ import annotations

import copy
import logging
import re

QUEST_XP = 60
MAX_OPEN = 3
MAX_STEPS = 5
GOLD_CAP = 1_000_000
_TAG_RE = re.compile(r"\[(QUEST|GOLD):([^\]]*)\]", re.I)
_ID_RE = re.compile(r"[^\w\-]+", re.U)

QUESTS_RULES = (
    "ПОРУЧЕНИЯ И КАЗНА. Побочное поручение с понятной целью: [QUEST:ADD;ID:id;TITLE:название;GOAL:что сделать;FROM:кто дал]. "
    "Сдвиг [QUEST:STEP;ID:id;TEXT:что изменилось]; итог только по факту: [QUEST:DONE;ID:id;EVIDENCE:факт] или "
    f"[QUEST:FAIL;ID:id;REASON:причина]. Открытых не больше {MAX_OPEN}; главная цель миссии — не поручение. "
    "Деньги партии: [GOLD:DELTA;AMOUNT:+30;CAUSE:награда] или AMOUNT:-10 за покупку; в минус казна не уходит."
)


def _clean(value, limit=160) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ;")[:limit]


def _quest_id(value) -> str:
    return _ID_RE.sub("-", str(value or "").strip().casefold()).strip("-")[:40]


def _archived_gold(session) -> int:
    try:
        from AI import dnd_campaign as campaign

        return max(0, int((campaign._chat_history(session.chat_id) or {}).get("treasury") or 0))
    except Exception:
        return 0


def ensure(session) -> None:
    if not isinstance(getattr(session, "quests", None), dict):
        session.quests = {}
    if not isinstance(getattr(session, "party_gold", None), int) or isinstance(getattr(session, "party_gold", None), bool):
        session.party_gold = _archived_gold(session)


def open_quests(session) -> list[tuple[str, dict]]:
    return [(key, row) for key, row in (getattr(session, "quests", None) or {}).items()
            if isinstance(row, dict) and row.get("status") == "open"]


def _fields(raw: str) -> tuple[str, dict]:
    parts = [part for part in str(raw).split(";")]
    verb = _clean(parts[0], 20).upper()
    fields = {}
    for part in parts[1:]:
        if ":" in part:
            key, value = part.split(":", 1)
            fields[key.strip().upper()] = value.strip()
    return verb, fields


def _award_quest_xp(session) -> list[str]:
    from AI import dnd_progression as progression

    gains: dict[str, int] = {}
    for key in progression._participant_keys(session):
        if progression._alive_active(session, key):
            progression._award(session, key, QUEST_XP, gains)
    return sorted(gains)


def apply_quest_metadata(session, original_text, cleaned, notices):
    """Validate and apply QUEST/GOLD tags; strip them from the visible text."""
    ensure(session)
    text = str(cleaned or "")
    extra = []
    scene = int(getattr(session, "scene_count", 0) or 0)
    for match in _TAG_RE.finditer(str(original_text or "")):
        kind = match.group(1).upper()
        verb, fields = _fields(match.group(2))
        try:
            if kind == "GOLD":
                if verb != "DELTA":
                    continue
                amount = int(str(fields.get("AMOUNT") or "0").replace(" ", "").replace("+", ""))
                if amount == 0 or abs(amount) > GOLD_CAP:
                    continue
                if session.party_gold + amount < 0:
                    extra.append(f"💰 Казне не хватает на это ({session.party_gold}); трата не проведена.")
                    continue
                session.party_gold = min(GOLD_CAP, session.party_gold + amount)
                cause = _clean(fields.get("CAUSE"), 80)
                extra.append(f"💰 Казна: {amount:+d}" + (f" ({cause})" if cause else "") + f" → {session.party_gold}")
                continue
            quest_id = _quest_id(fields.get("ID"))
            if not quest_id:
                continue
            row = session.quests.get(quest_id)
            if verb == "ADD":
                title = _clean(fields.get("TITLE"), 80)
                if not title or (row and row.get("status") == "open") or len(open_quests(session)) >= MAX_OPEN:
                    continue
                session.quests[quest_id] = {"title": title, "goal": _clean(fields.get("GOAL"), 200),
                                            "giver": _clean(fields.get("FROM"), 80), "status": "open",
                                            "steps": [], "opened_scene": scene}
                extra.append(f"📜 Новое поручение: «{title}»")
            elif not isinstance(row, dict) or row.get("status") != "open":
                continue
            elif verb == "STEP":
                step = _clean(fields.get("TEXT"), 160)
                if step:
                    row["steps"] = (list(row.get("steps") or []) + [step])[-MAX_STEPS:]
            elif verb == "DONE":
                evidence = _clean(fields.get("EVIDENCE"), 200)
                if not evidence:
                    continue  # completion needs a fact
                row.update(status="done", evidence=evidence, closed_scene=scene)
                rewarded = _award_quest_xp(session)
                extra.append(f"✅ Поручение выполнено: «{row['title']}»"
                             + (f" (+{QUEST_XP} опыта каждому)" if rewarded else ""))
            elif verb == "FAIL":
                row.update(status="failed", evidence=_clean(fields.get("REASON"), 200), closed_scene=scene)
                extra.append(f"❌ Поручение провалено: «{row['title']}»")
        except (TypeError, ValueError):
            logging.info("DnD quest tag rejected chat_id=%s tag=%s", getattr(session, "chat_id", None), match.group(0)[:200])
    return _TAG_RE.sub("", text).strip(), list(notices or []) + extra


def context_lines(session) -> list[str]:
    lines = []
    for key, row in open_quests(session):
        step = f"; последнее: {row['steps'][-1]}" if row.get("steps") else ""
        giver = f" (от {row['giver']})" if row.get("giver") else ""
        lines.append(f"- {key}: «{row.get('title')}»{giver} — {row.get('goal') or 'цель не уточнена'}{step}")
    gold = getattr(session, "party_gold", None)
    if isinstance(gold, int):
        lines.append(f"- казна партии: {gold}")
    return lines


def render(session) -> str:
    ensure(session)
    quests = session.quests
    lines = ["📜 Поручения партии", f"💰 Казна: {session.party_gold}"]
    groups = (("open", "Открытые"), ("done", "Выполнено"), ("failed", "Провалено"))
    for status, label in groups:
        rows = [row for row in quests.values() if isinstance(row, dict) and row.get("status") == status]
        if not rows:
            continue
        lines.append(f"\n{label}:")
        for row in rows:
            giver = f" — от {row['giver']}" if row.get("giver") else ""
            lines.append(f"• «{row.get('title')}»{giver}")
            detail = row.get("goal") if status == "open" else row.get("evidence")
            if detail:
                lines.append("  " + _clean(detail, 200))
            if status == "open" and row.get("steps"):
                lines.append("  ↳ " + row["steps"][-1])
    if len(lines) == 2:
        lines.append("Поручений пока нет — мастер выдаёт их по ходу сюжета.")
    return "\n".join(lines)


def report_section(session) -> str:
    quests = [row for row in (getattr(session, "quests", None) or {}).values() if isinstance(row, dict)]
    if not quests and not getattr(session, "party_gold", 0):
        return ""
    done = [row["title"] for row in quests if row.get("status") == "done"]
    failed = [row["title"] for row in quests if row.get("status") == "failed"]
    still = [row["title"] for row in quests if row.get("status") == "open"]
    lines = ["📜 Поручения"]
    if done:
        lines.append("Выполнено: " + ", ".join(f"«{title}»" for title in done))
    if failed:
        lines.append("Провалено: " + ", ".join(f"«{title}»" for title in failed))
    if still:
        lines.append("Осталось открытым: " + ", ".join(f"«{title}»" for title in still))
    lines.append(f"💰 Казна партии: {int(getattr(session, 'party_gold', 0) or 0)}")
    return "\n".join(lines)


def archive_treasury(session, _players) -> None:
    try:
        from AI import dnd_campaign as campaign

        campaign._chat_history(session.chat_id, True)["treasury"] = int(getattr(session, "party_gold", 0) or 0)
    except Exception:
        logging.exception("DnD treasury archive failed chat_id=%s", getattr(session, "chat_id", None))


def install_dnd_quests(*, state_policy, metadata_policy) -> None:
    """Install before progression so a completed quest levels heroes in the same turn."""
    from AI.dnd_adventure import register_report_section
    from AI.dnd_progression import register_archive_hook
    from AI.dnd_prompt_registry import PromptSection, is_participant_session, register_prompt_section

    register_prompt_section(PromptSection("quests", QUESTS_RULES, order=75, when=is_participant_session,
                                          tags=("QUEST", "GOLD"), optional=True))
    if getattr(state_policy, "_upupa_dnd_quests", False):
        return
    state_policy.add_ensure_hook(ensure)
    state_policy.add_state_field("quests", lambda s: copy.deepcopy(getattr(s, "quests", None) or {}))
    state_policy.add_state_field("party_gold", lambda s: int(getattr(s, "party_gold", 0) or 0))
    metadata_policy.add_postprocessor(apply_quest_metadata)
    register_report_section(report_section)
    register_archive_hook(archive_treasury)
    state_policy._upupa_dnd_quests = True


__all__ = ["QUESTS_RULES", "apply_quest_metadata", "context_lines", "install_dnd_quests", "open_quests", "render",
           "report_section"]
