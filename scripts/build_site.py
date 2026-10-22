#!/usr/bin/env python3
"""Собрать статический сайт Упупы для GitHub Pages.

Берёт ``site/`` как есть и подставляет в ``index.html`` справочник команд,
разобранный из ``prompts.HELP_DICT``: сайт показывает ту же справку, что и бот,
и не расходится с ней после правок в ``prompts/help_texts.py``.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = REPO_ROOT / "site"
DEFAULT_OUT_DIR = REPO_ROOT / "_site"
HELP_PLACEHOLDER = "__UPUPA_HELP_JSON__"

# Названия вкладок справочника. Первый заголовок раздела в HELP_DICT не всегда
# описывает весь раздел («ВЕКТОРИНЫ» открывают игры, «ПАГОДА» — всякую утилиту).
SECTION_LABELS = {
    "main": "Асновы",
    "talking": "Говорилка",
    "stats": "Стотистика",
    "social": "Соцграф",
    "world": "Мир Упупы",
    "creative": "Егры",
    "media": "Медиа",
    "content": "Коналы",
    "utils": "Всякое",
}

_HEADING_RE = re.compile(r"^(?P<prefix>[^<]*)<b>(?P<title>.+?)</b>\s*$")
_CODE_RE = re.compile(r"<code>(.*?)</code>")
_LEADING_SYMBOLS_RE = re.compile(r"^(?P<emoji>[^\w\s«]+)\s*(?P<rest>.*)$")


def _split_emoji(text: str) -> tuple[str, str]:
    text = text.strip().rstrip(":").strip()
    match = _LEADING_SYMBOLS_RE.match(text)
    if not match:
        return "", text
    return match["emoji"], match["rest"].strip()


def _inline_html(text: str) -> str:
    """Экранировать строку справки, оставив только ``<code>``."""
    text = re.sub(r"</?[ib]>", "", text)
    escaped = html.escape(text, quote=False)
    return escaped.replace("&lt;code&gt;", "<code>").replace("&lt;/code&gt;", "</code>")


def _code_spans(text: str) -> list[str]:
    # «<code>досье</code> / <code>досье @user</code> / <code>досье</code> реплаем»
    # повторяет команду ради подписи, на сайте хватит одной плашки.
    spans = (html.unescape(span).strip() for span in _CODE_RE.findall(text))
    return list(dict.fromkeys(span for span in spans if span))


def _split_item(line: str) -> tuple[str, str] | None:
    """Разделить «<code>команды</code> - описание» по первому дефису вне ``<code>``."""
    for match in re.finditer(r" - ", line):
        left = line[: match.start()]
        if "</code>" in left and left.count("<code>") == left.count("</code>"):
            return left, line[match.end():]
    return None


def _parse_item(line: str) -> dict:
    parts = _split_item(line)
    if parts is None:
        # Строка-перечисление вроде «кот / кошка · выдра · крол»: только команды.
        commands = [
            command.strip()
            for span in _code_spans(line)
            for command in span.split(" / ")
            if command.strip()
        ]
        return {"cmds": commands, "qual": "", "desc": ""}

    left, description = parts
    qualifier = _CODE_RE.sub(" ", left)
    qualifier = re.sub(r"[/()·]", " ", qualifier)
    return {
        "cmds": _code_spans(left),
        "qual": " ".join(qualifier.split()),
        "desc": _inline_html(description.strip()),
    }


def parse_help_section(text: str) -> list[dict]:
    """Разобрать один раздел HELP_DICT на группы с командами."""
    groups: list[dict] = []
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            emoji, title = _split_emoji(f"{heading['prefix']} {heading['title']}")
            current = {"emoji": emoji, "title": title, "items": [], "notes": []}
            groups.append(current)
            continue
        if current is None:
            current = {"emoji": "", "title": "", "items": [], "notes": []}
            groups.append(current)
        if line.startswith("<code>"):
            current["items"].append(_parse_item(line))
        else:
            current["notes"].append(_inline_html(line))
    # Группы без команд — это шапка справки с подписью бота, на сайте она не нужна.
    return [group for group in groups if group["items"]]


def build_help_data(help_dict: dict[str, str]) -> list[dict]:
    sections = []
    for key, text in help_dict.items():
        groups = parse_help_section(text)
        if not groups:
            continue
        sections.append({
            "key": key,
            "title": SECTION_LABELS.get(key) or groups[0]["title"],
            "groups": groups,
        })
    return sections


def render_index(template: str, sections: list[dict]) -> str:
    if template.count(HELP_PLACEHOLDER) != 1:
        raise ValueError(f"в site/index.html должен быть ровно один {HELP_PLACEHOLDER}")
    payload = json.dumps(sections, ensure_ascii=False, separators=(",", ":"))
    # Внутри <script> нельзя оставлять «</», иначе браузер закроет тег раньше времени.
    payload = payload.replace("</", "<\\/")
    return template.replace(HELP_PLACEHOLDER, payload)


def build_site(out_dir: Path, help_dict: dict[str, str]) -> list[dict]:
    out_dir = out_dir.resolve()
    if out_dir in (REPO_ROOT, SITE_DIR) or out_dir in REPO_ROOT.parents:
        raise ValueError(f"не стану перезаписывать {out_dir}")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(SITE_DIR, out_dir)

    sections = build_help_data(help_dict)
    index_path = out_dir / "index.html"
    index_path.write_text(
        render_index(index_path.read_text(encoding="utf-8"), sections),
        encoding="utf-8",
    )
    return sections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="куда сложить сайт")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))
    from prompts import HELP_DICT

    sections = build_site(args.out, HELP_DICT)
    commands = sum(len(group["items"]) for section in sections for group in section["groups"])
    print(f"сайт собран в {args.out}: {len(sections)} разделов, {commands} строк команд")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
