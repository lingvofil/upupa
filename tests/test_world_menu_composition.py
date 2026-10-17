from pathlib import Path
import re

from features.world.hub_ui import build_world_main_markup
from handlers import world_expansion, world_hub, world_interactions


ROOT = Path(__file__).resolve().parents[1]
WORLD_HANDLER_PATHS = sorted((ROOT / "handlers").glob("world*.py"))


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
        ("🏆 Рейтинг", "worldhub:rating"),
        ("🚫 Санкции", "worldhub:sanctions"),
        ("⚖️ Международный суд", "worldhub:court"),
        ("🧱 Блоки", "worldhub:blocs"),
        ("🗺 Карта мира", "worldhub:map"),
        ("📰 Мировые новости", "worldhub:news"),
        ("📜 Хроника", "worldhub:chronicle"),
    ]
    assert _signature(world_interactions._main_markup()) == expected
    assert _signature(world_expansion._main_markup()) == expected
    assert _signature(world_hub._main_markup()) == expected


def test_each_world_command_and_callback_prefix_has_exactly_one_router():
    """Duplicate filters across routers made whole handlers silently dead before."""
    owners: dict[str, set[str]] = {}
    for path in WORLD_HANDLER_PATHS:
        source = path.read_text(encoding="utf-8")
        for literal in re.findall(r'"(упупа миры|государство|государства|дипломатия)"', source):
            owners.setdefault(f"text:{literal}", set()).add(path.name)
        for prefix in re.findall(r'F\.data\.startswith\("([a-z:]+)"\)', source):
            owners.setdefault(f"callback:{prefix}", set()).add(path.name)
    duplicated = {key: names for key, names in owners.items() if len(names) > 1}
    assert duplicated == {}


def test_hub_screens_are_rendered_only_by_world_hub():
    for path in WORLD_HANDLER_PATHS:
        source = path.read_text(encoding="utf-8")
        if path.name == "world_hub.py":
            assert 'F.data.startswith("worldhub:")' in source
        else:
            assert 'F.data.startswith("worldhub:")' not in source
