import json
import re
from pathlib import Path

import pytest

from prompts import HELP_DICT
from scripts import build_site


ROOT = Path(__file__).resolve().parents[1]


def _embedded_help(index_html: str) -> list[dict]:
    match = re.search(
        r'<script id="help-data" type="application/json">(.*?)</script>',
        index_html,
        re.S,
    )
    assert match, "в index.html нет справочника"
    return json.loads(match.group(1))


def _items(sections: list[dict]) -> list[dict]:
    return [item for section in sections for group in section["groups"] for item in group["items"]]


def test_every_help_section_reaches_the_site():
    sections = build_site.build_help_data(HELP_DICT)

    assert [section["key"] for section in sections] == list(HELP_DICT)
    assert all(section["title"] for section in sections)


def test_every_help_command_reaches_the_site():
    sections = build_site.build_help_data(HELP_DICT)
    site_commands = {command for item in _items(sections) for command in item["cmds"]}

    help_commands = set()
    for text in HELP_DICT.values():
        for span in re.findall(r"<code>(.*?)</code>", text):
            help_commands.update(part.strip() for part in span.split(" / "))

    assert help_commands <= site_commands


def test_parsed_items_keep_descriptions_and_drop_telegram_markup():
    items = _items(build_site.build_help_data(HELP_DICT))
    by_command = {command: item for item in items for command in item["cmds"]}

    assert by_command["нарисуй [промпт]"]["desc"] == "генерирую изображение"
    assert by_command["переведи"]["qual"] == "реплаем"
    assert "<b>" not in json.dumps(items, ensure_ascii=False)
    assert "<i>" not in json.dumps(items, ensure_ascii=False)


def test_header_line_without_commands_is_not_published():
    sections = build_site.build_help_data(HELP_DICT)
    notes = [note for section in sections for group in section["groups"] for note in group["notes"]]

    assert sections[0]["groups"][0]["title"] == "Асновы"
    assert not any("мой конал" in note for note in notes)


def test_item_parser_splits_on_dash_outside_code():
    item = build_site._parse_item(
        "<code>переведи</code> (реплаем) - перевод на русский, а русский - на блатной жаргон"
    )

    assert item == {
        "cmds": ["переведи"],
        "qual": "реплаем",
        "desc": "перевод на русский, а русский - на блатной жаргон",
    }


def test_item_parser_handles_enumerations_and_repeated_commands():
    animals = build_site._parse_item("<code>кот / кошка</code> · <code>выдра</code>")
    dossier = build_site._parse_item(
        "<code>досье</code> / <code>досье @user</code> / <code>досье</code> реплаем - карточка человека"
    )

    assert animals == {"cmds": ["кот", "кошка", "выдра"], "qual": "", "desc": ""}
    assert dossier["cmds"] == ["досье", "досье @user"]
    assert dossier["qual"] == "реплаем"


def test_descriptions_are_escaped_except_code():
    item = build_site._parse_item("<code>x</code> - a <script> & <code>упупа настройки</code>")

    assert item["desc"] == "a &lt;script&gt; &amp; <code>упупа настройки</code>"


def test_render_index_requires_single_placeholder_and_escapes_script_end():
    sections = [{"key": "k", "title": "</script>", "groups": []}]

    rendered = build_site.render_index(f"<script>{build_site.HELP_PLACEHOLDER}</script>", sections)

    assert rendered.count("</script>") == 1
    with pytest.raises(ValueError):
        build_site.render_index("<html></html>", sections)


def test_build_site_writes_page_with_embedded_help(tmp_path):
    out_dir = tmp_path / "site"

    sections = build_site.build_site(out_dir, HELP_DICT)

    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert build_site.HELP_PLACEHOLDER not in index_html
    assert _embedded_help(index_html) == sections
    for asset in ("style.css", "app.js", "favicon.svg"):
        assert (out_dir / asset).is_file()


def test_site_does_not_link_to_source_repository():
    for path in (ROOT / "site").iterdir():
        assert "github.com" not in path.read_text(encoding="utf-8"), path.name


def test_build_site_refuses_to_wipe_repository():
    with pytest.raises(ValueError):
        build_site.build_site(ROOT, HELP_DICT)
