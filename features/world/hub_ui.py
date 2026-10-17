"""Inline keyboards for the World of Upupa — one definition per screen."""

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder


STATES_PAGE_SIZE = 8
TARGETS_PAGE_SIZE = 10


def build_world_main_markup() -> types.InlineKeyboardMarkup:
    """The single canonical main menu used by every World entry point."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🏳 Моё государство", callback_data="worldhub:mine")
    builder.button(text="🌐 Государства", callback_data="worldhub:states")
    builder.button(text="🤝 Дипломатия", callback_data="worldhub:diplomacy")
    builder.button(text="🏆 Рейтинг", callback_data="worldhub:rating")
    builder.button(text="🚫 Санкции", callback_data="worldhub:sanctions")
    builder.button(text="⚖️ Международный суд", callback_data="worldhub:court")
    builder.button(text="🧱 Блоки", callback_data="worldhub:blocs")
    builder.button(text="🗺 Карта мира", callback_data="worldhub:map")
    builder.button(text="📰 Мировые новости", callback_data="worldhub:news")
    builder.button(text="📜 Хроника", callback_data="worldhub:chronicle")
    builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup()


def back_markup(target: str = "main", label: str = "⬅️ В Мир Упупы") -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=label, callback_data=f"worldhub:{target}")
    return builder.as_markup()


def own_state_markup(*, has_symbol: bool) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎩 Посол", callback_data="worldhub:ambassador")
    builder.button(text="🏴 Флаг / герб", callback_data="worldsymbol:menu")
    if has_symbol:
        builder.button(text="👁 Показать символику", callback_data="worldhub:flag")
    builder.button(text="⚙️ Настройки государства", callback_data="worldhub:settings")
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    builder.adjust(2, *([1] * (3 if has_symbol else 2)))
    return builder.as_markup()


def diplomacy_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🤝 Предложить союз", callback_data="worldx:choose:alliance")
    builder.button(text="💔 Разорвать союз", callback_data="worldx:choose:break")
    builder.button(text="⚔️ Объявить войну", callback_data="worldx:choose:war")
    builder.button(text="🥊 Начать битву", callback_data="worldx:choose:battle")
    builder.button(text="🕊 Предложить мир", callback_data="worldx:choose:peace")
    builder.button(text="🏳 Капитулировать", callback_data="worldx:choose:capitulate")
    builder.button(text="🛬 Пригласить в гости", callback_data="worldx:choose:invite")
    builder.button(text="🎁 Отправить дар", callback_data="worldx:choose:gift")
    builder.button(text="🖕 Оскорбить государство", callback_data="worldx:choose:insult")
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    builder.adjust(2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def sanctions_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚫 Ввести санкции", callback_data="worldx:choose:sanction")
    builder.button(text="🕊 Снять санкции", callback_data="worldx:choose:unsanction")
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    builder.adjust(2, 1)
    return builder.as_markup()


def blocs_markup(*, member: bool, can_apply: bool) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if member:
        builder.button(text="🚪 Покинуть блок", callback_data="worldbloc:leave")
    elif can_apply:
        builder.button(text="📝 Подать заявку в блок", callback_data="worldbloc:choose")
    builder.button(text="⬅️ В Мир Упупы", callback_data="worldhub:main")
    builder.adjust(1)
    return builder.as_markup()


def settings_markup(*, share_identity: bool, share_activity: bool, newspaper: bool) -> types.InlineKeyboardMarkup:
    def mark(value: bool) -> str:
        return "✅" if value else "❌"

    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"{mark(share_identity)} Характеристика видна другим",
        callback_data="worldhub:set:share_identity",
    )
    builder.button(
        text=f"{mark(share_activity)} Активность видна другим",
        callback_data="worldhub:set:share_activity",
    )
    builder.button(text=f"{mark(newspaper)} Еженедельная газета", callback_data="worldhub:set:newspaper")
    builder.button(text="⬅️ К государству", callback_data="worldhub:mine")
    builder.adjust(1)
    return builder.as_markup()


def paged_markup(
    items: list,
    *,
    page: int,
    page_size: int,
    label,
    callback,
    nav_prefix: str,
    tail: list[tuple[str, str]] | None = None,
) -> tuple[types.InlineKeyboardMarkup, int, int]:
    """One page of item buttons, ◀ ▶ navigation and trailing buttons.

    Returns ``(markup, page, pages)`` with the page clamped to the valid range.
    """
    pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    visible = items[page * page_size:(page + 1) * page_size]
    builder = InlineKeyboardBuilder()
    for item in visible:
        builder.button(text=label(item), callback_data=callback(item))
    nav = 0
    if page > 0:
        builder.button(text="◀️", callback_data=f"{nav_prefix}:{page - 1}")
        nav += 1
    if page < pages - 1:
        builder.button(text="▶️", callback_data=f"{nav_prefix}:{page + 1}")
        nav += 1
    for text, data in tail or []:
        builder.button(text=text, callback_data=data)
    sizes = [1] * len(visible)
    if nav:
        sizes.append(nav)
    sizes.extend([1] * len(tail or []))
    builder.adjust(*sizes)
    return builder.as_markup(), page, pages
