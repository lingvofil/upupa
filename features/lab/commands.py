"""Команда «лаба»: разбор и применение без aiogram.

    лаба                — что включено в этом чате
    лаба вкл <имя>      — включить эксперимент
    лаба выкл <имя>     — выключить эксперимент
    лаба выкл все       — выключить всё в этом чате

Имя — ключ (``oracle``) или русский псевдоним (``оракул``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from features.lab.flags import ExperimentFlags
from features.lab.registry import EXPERIMENTS, find_experiment

_PATTERN = re.compile(r"^лаба(?:\s+(вкл|выкл)(?:\s+(\S+))?)?$")
_ALL = {"все", "всё"}


@dataclass(frozen=True)
class LabCommand:
    action: str  # status | enable | disable | disable_all
    target: str | None = None


@dataclass(frozen=True)
class LabResult:
    text: str
    # (ключ, включён ли теперь) для каждого эксперимента, который реально переключился.
    toggled: tuple[tuple[str, bool], ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return bool(self.toggled)


def parse_lab_command(text: str | None) -> LabCommand | None:
    if not text:
        return None
    normalized = " ".join(text.strip().lower().split())
    match = _PATTERN.match(normalized)
    if match is None:
        return None
    verb, target = match.groups()
    if verb is None or target is None:
        return LabCommand("status")
    if verb == "выкл" and target in _ALL:
        return LabCommand("disable_all")
    return LabCommand("enable" if verb == "вкл" else "disable", target)


def _status_text(flags: ExperimentFlags, chat_id: int | str) -> str:
    lines = ["Лаба в этом чате:"]
    for experiment in EXPERIMENTS:
        mark = "✅" if flags.is_enabled(chat_id, experiment.key) else "❌"
        aliases = f" ({', '.join(experiment.aliases)})" if experiment.aliases else ""
        lines.append(f"{mark} {experiment.key}{aliases} — {experiment.title}")
    lines.append("")
    lines.append("лаба вкл <имя> · лаба выкл <имя> · лаба выкл все")
    return "\n".join(lines)


def apply_lab_command(flags: ExperimentFlags, chat_id: int | str, command: LabCommand) -> LabResult:
    if command.action == "status":
        return LabResult(_status_text(flags, chat_id))

    if command.action == "disable_all":
        disabled = flags.disable_all(chat_id)
        if not disabled:
            return LabResult("Тут ничего не включено.")
        return LabResult(
            "Выключил всё: " + ", ".join(disabled) + ".",
            tuple((key, False) for key in disabled),
        )

    experiment = find_experiment(command.target or "")
    if experiment is None:
        known = ", ".join(experiment.key for experiment in EXPERIMENTS)
        return LabResult(f"Не знаю эксперимент «{command.target}». Есть: {known}.")

    if command.action == "enable":
        if not flags.enable(chat_id, experiment.key):
            return LabResult(f"{experiment.title}: уже включено.")
        return LabResult(f"{experiment.title}: включено в этом чате.", ((experiment.key, True),))

    if not flags.disable(chat_id, experiment.key):
        return LabResult(f"{experiment.title}: и так выключено.")
    return LabResult(f"{experiment.title}: выключено.", ((experiment.key, False),))
