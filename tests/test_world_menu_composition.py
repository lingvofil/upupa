from pathlib import Path

from features.world.hub_ui import build_world_main_markup
from handlers import world_expansion, world_hub, world_interactions


ROOT = Path(__file__).resolve().parents[1]
WORLD_HANDLER_PATHS = (
    ROOT / "handlers" / "world_interactions.py",
    ROOT / "handlers" / "world_expansion.py",
    ROOT / "handlers" / "world_hub.py",
)


def _signature(markup):
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard
        for button in row
    ]


def test_all_world_hub_paths_use_the_same_canonical_main_menu():
    expected = _signature(build_world_main_markup())

    assert expected == [
        ("🏳 Моё государство", "worldhub:mine"),
        ("🌐 Государства", "worldhub:states"),
        ("🤝 Дипломатия", "worldhub:diplomacy"),
        ("🎩 Назначить посла", "worldx:ambassador"),
        ("🏴 Флаг / герб", "worldsymbol:menu"),
        ("🚫 Санкции", "worldhub:sanctions"),
        ("⚖️ Международный суд", "worldhub:court"),
        ("🗺 Карта мира", "worldhub:map"),
        ("📰 Мировые новости", "worldhub:news"),
        ("📜 Хроника", "worldhub:chronicle"),
    ]
    assert _signature(world_interactions._main_markup()) == expected
    assert _signature(world_expansion._main_markup()) == expected
    assert _signature(world_hub._main_markup()) == expected


def test_world_handlers_use_canonical_menu_without_package_monkeypatches():
    package_source = (ROOT / "handlers" / "__init__.py").read_text(encoding="utf-8")

    assert "._main_markup =" not in package_source
    assert "build_world_main_markup" not in package_source

    for path in WORLD_HANDLER_PATHS:
        source = path.read_text(encoding="utf-8")
        assert "from features.world.hub_ui import build_world_main_markup" in source
        assert "return build_world_main_markup()" in source


def test_world_router_precedence_is_unchanged():
    source = (ROOT / "handlers" / "__init__.py").read_text(encoding="utf-8")

    assert source.index("world_interactions.router") < source.index("world_expansion.router")
    assert source.index("world_expansion.router") < source.index("world_symbols.router")
    assert source.index("world_symbols.router") < source.index("world_hub.router")
