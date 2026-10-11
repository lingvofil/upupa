"""Single registry of DnD protocol sections that actually reach the provider.

Historically every mechanics module appended its rules to
``dnd.DND_SYSTEM_PROMPT``. The live generation path, however, sends only the
compact contract built by ``build_compact_system``. A rule that existed only in
the long prompt (for example the GROWTH tag) silently never reached the model.

Modules now register a compact ``PromptSection`` here. ``build_compact_system``
assembles the provider system block exclusively from this registry, and the
tag-coverage test asserts that every tag the code parses is taught by at least
one registered section.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

Predicate = Callable[[object, str], bool]


@dataclass(frozen=True)
class PromptSection:
    """One compact protocol block.

    ``when`` receives ``(session, casefolded_prompt)``; ``None`` means always.
    ``order`` keeps a deterministic position: lower comes first.
    ``tags`` documents which parsed tag heads this section teaches.
    ``optional`` sections are appended only when budget remains; mandatory
    ones are part of the non-truncatable protocol and must stay compact.
    """

    name: str
    text: str
    order: int = 100
    when: Predicate | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    optional: bool = False

    def active(self, session, prompt: str) -> bool:
        if self.when is None:
            return True
        try:
            return bool(self.when(session, prompt))
        except Exception:
            # A broken predicate must not hide a mandatory protocol section.
            return True


_SECTIONS: dict[str, PromptSection] = {}


def register_prompt_section(section: PromptSection) -> PromptSection:
    """Register or replace a section by name (idempotent across reinstalls)."""
    text = str(section.text or "").strip()
    if not text:
        raise ValueError(f"Prompt section {section.name!r} is empty")
    _SECTIONS[section.name] = PromptSection(
        name=section.name,
        text=text,
        order=int(section.order),
        when=section.when,
        tags=tuple(str(tag).upper() for tag in section.tags),
        optional=bool(section.optional),
    )
    return _SECTIONS[section.name]


def unregister_prompt_section(name: str) -> None:
    _SECTIONS.pop(name, None)


def all_sections() -> list[PromptSection]:
    return sorted(_SECTIONS.values(), key=lambda row: (row.order, row.name))


def active_sections(session, prompt: str = "") -> list[PromptSection]:
    text = str(prompt or "").casefold()
    return [row for row in all_sections() if row.active(session, text)]


def taught_tags() -> set[str]:
    tags: set[str] = set()
    for row in _SECTIONS.values():
        tags.update(row.tags)
    return tags


def is_participant_session(session, _prompt: str = "") -> bool:
    return getattr(session, "mode", None) == "participants"


__all__ = [
    "PromptSection",
    "active_sections",
    "all_sections",
    "is_participant_session",
    "register_prompt_section",
    "taught_tags",
    "unregister_prompt_section",
]
