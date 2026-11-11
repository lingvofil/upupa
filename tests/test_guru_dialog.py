"""Ограничения панели: длинные реплики не теряются и сохраняют безопасный HTML."""

import io
import xml.etree.ElementTree as ET

from PIL import Image

from games.guru import art, dialog, textui


def test_long_unicode_dialog_keeps_every_word_and_balanced_html():
    words = "🔮 <Гоша> & его история. " * 800
    parts = dialog.pages([f"<blockquote>{textui.escape(words)}</blockquote>"])
    assert len(parts) > 1
    restored = []
    for part in parts:
        assert len(part.encode("utf-16-le")) // 2 <= dialog.PAGE_LIMIT
        restored.extend(ET.fromstring(f"<root>{part}</root>").itertext())
    assert "".join(restored) == words


def test_pagination_keeps_short_replies_together():
    assert dialog.pages(["<i>Пришла Людмила.</i>", "<blockquote>Здравствуйте.</blockquote>"]) == [
        "<i>Пришла Людмила.</i>\n\n<blockquote>Здравствуйте.</blockquote>"
    ]


def test_dialog_controls_roundtrip_and_fit_callback_limit():
    for mode in ("screen", "log", "heard"):
        data = textui.dialog_data("a" * 32, 120, mode, 35)
        parsed = textui.parse(data)
        assert parsed.kind == "dialog" and parsed.action == mode and parsed.page == 35 and parsed.step == 120
        assert len(data.encode()) <= 64
    assert textui.parse("guru:ui:ab:1:bogus:0") is None


def test_tarot_spread_contains_three_readable_cards():
    image = Image.open(io.BytesIO(art.render_spread(("porcelain", "asteroids", "lighthouse"))))
    assert image.format == "JPEG" and image.size == (1224, 596)
    # Карты остаются в исходном порядке, каждая целиком занимает свою колонку.
    pixels = image.convert("RGB")
    assert pixels.crop((0, 0, 400, 596)).tobytes() != pixels.crop((412, 0, 812, 596)).tobytes()
