from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "index.html"


def _source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_crocodile_palette_is_rendered_without_javascript_bootstrap():
    source = _source()
    toolbar = source.split('<div id="toolbar" class="toolbar">', 1)[1]
    toolbar = toolbar.split("<script>", 1)[0]

    assert toolbar.count('class="swatch') == 14
    assert 'id="colorPicker"' in toolbar
    assert 'data-c="#000000"' in toolbar
    assert 'data-c="#ff2d55"' in toolbar


def test_crocodile_webapp_tolerates_partial_telegram_api():
    source = _source()

    assert "window.Telegram && window.Telegram.WebApp" in source
    assert 'typeof tg.expand === "function"' in source
    assert 'typeof tg.ready === "function"' in source
    assert 'getHashParam("tgWebAppData")' in source
    assert "showTelegramAlert" in source
    assert "showWordPopup" in source
    assert "closeWebApp" in source
