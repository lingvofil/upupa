"""Развитие салона: что можно купить за деньги из кассы и что это даёт. Без Telegram.

Покупка остаётся навсегда и работает со следующей партии. Ядро получает список ключей
купленного и превращает его в ``Perks``; где хранятся покупки, оно не знает.

В живом приёме клиент — человек, поэтому настроение и реклама там не работают. Действуют
надбавка к заработку, лишнее гадание и инструменты, от которых точнее толкования.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

_NO_QUALITY: Mapping[str, float] = MappingProxyType({})


@dataclass(frozen=True)
class Perks:
    fee: float = 0.0  # надбавка к заработку, доля
    clients: int = 0  # лишние клиенты в смене
    questions: int = 0  # лишние уточнения на клиента
    readings: int = 0  # лишние гадания на клиента
    quality: Mapping[str, float] = field(default_factory=lambda: _NO_QUALITY)  # метод → чаще верные толкования
    accuracy: float = 0.0  # реакции клиента честнее
    deadline: float = 0.0  # гороскоп подсказывает срок, с такой вероятностью верный
    trust: int = 0  # стартовое доверие
    satisfaction: int = 0  # стартовое довольство
    soft_questions: bool = False  # вопросы не портят настроение, скептик записывает их реже
    skeptic: float = 1.0  # множитель шанса скептика
    forgive: int = 0  # сколько разоблачений за смену обходятся без штрафа и потери легенды
    rituals: tuple[str, ...] = ()  # реплики при входе клиента; {name} — его имя


NO_PERKS = Perks()


@dataclass(frozen=True)
class Item:
    key: str
    emoji: str
    name: str
    short: str  # подпись на кнопке
    price: int
    group: str
    note: str  # что даёт
    perks: Perks


TOOL_QUALITY = 0.6  # хороший инструмент почти всегда оставляет два толкования, и верное среди них
PROPS = "props"
PROMO = "promo"
COMFORT = "comfort"
GROUP_TITLES = MappingProxyType({PROPS: "🔮 Реквизит: точнее гадать", PROMO: "📣 Реклама: больше клиентов и денег", COMFORT: "🛋 Уют: настроение клиента"})

ITEMS: Mapping[str, Item] = MappingProxyType({item.key: item for item in (
    Item("deck", "🃏", "Колода с золотым обрезом", "Колода", 3500, PROPS,
         "Таро почти всегда указывает верно", Perks(quality=MappingProxyType({"tarot": TOOL_QUALITY}))),
    Item("loupe", "🔍", "Лупа хироманта", "Лупа", 2500, PROPS,
         "ладонь почти всегда читается верно", Perks(quality=MappingProxyType({"palm": TOOL_QUALITY}))),
    Item("almanac", "🌙", "Лунный альманах", "Альманах", 5000, PROPS,
         "гороскоп точнее и подсказывает срок",
         Perks(quality=MappingProxyType({"horoscope": TOOL_QUALITY}), deadline=0.75)),
    Item("bowl", "🥣", "Поющая чаша", "Чаша", 2500, PROPS,
         "энергетика почти всегда звучит верно", Perks(quality=MappingProxyType({"energy": TOOL_QUALITY}))),
    Item("ledger", "📒", "Тетрадка сплетен", "Тетрадка", 12000, PROPS,
         "ещё один вопрос клиенту", Perks(questions=1)),
    Item("ball", "🔮", "Настоящий хрустальный шар", "Шар", 7000, PROPS,
         "ещё одно гадание на клиента", Perks(readings=1)),
    Item("mirror", "🪞", "Зеркало за спиной клиента", "Зеркало", 5000, PROPS,
         "реакции клиента честнее", Perks(accuracy=0.15)),
    Item("flyers", "📰", "Объявления на подъездах", "Объявления", 4000, PROMO,
         "заработок +10%", Perks(fee=0.10)),
    Item("neon", "💡", "Неоновая вывеска «МАГИЯ»", "Вывеска", 8000, PROMO,
         "заработок ещё +15%", Perks(fee=0.15)),
    Item("reviews", "⭐", "Купленные отзывы", "Отзывы", 18000, PROMO,
         "четвёртый клиент в смену", Perks(clients=1)),
    Item("zina", "🚪", "Тётя Зина на входе", "Тётя Зина", 2500, PROMO,
         "скептиков пускает вдвое реже", Perks(skeptic=0.5)),
    Item("candles", "🕯", "Свечи с запахом денег", "Свечи", 2500, COMFORT,
         "клиент сразу доверяет больше", Perks(trust=15, rituals=("🕯 На столе горят свечи. {name} перестаёт оглядываться.",))),
    Item("tea", "🫖", "Чай с чабрецом", "Чай", 3000, COMFORT,
         "сеанс нравится сильнее", Perks(satisfaction=18, rituals=("🫖 Мадам наливает чай с чабрецом. {name} греет руки о чашку.",))),
    Item("armchair", "🛋", "Кресло, из которого трудно встать", "Кресло", 4000, COMFORT,
         "вопросы не портят настроение клиента",
         Perks(soft_questions=True, satisfaction=6, rituals=("🛋 {name} садится в кресло и понимает, что встать будет непросто.",))),
    Item("cat", "🐈‍⬛", "Чёрный кот", "Кот", 6000, COMFORT,
         "первое разоблачение за смену обходится без штрафа и потери легенды",
         Perks(forgive=1, rituals=("🐈‍⬛ Чёрный кот забирается клиенту на колени. Сопротивление бесполезно.",))),
)})

# Реплика в чат при покупке. Игрок для клиентов — «мадам» независимо от пола.
PURCHASE_LINES: Mapping[str, str] = MappingProxyType({
    "deck": "🃏 Мадам {name} купила колоду с золотым обрезом. Карты теперь врут реже, а стоят как почка.",
    "loupe": "🔍 Мадам {name} купила лупу хироманта. На ладонях теперь видно даже долги.",
    "almanac": "🌙 Мадам {name} купила лунный альманах. Звёзды наконец-то называют сроки.",
    "bowl": "🥣 Мадам {name} купила поющую чашу. Соседи уже стучат.",
    "ledger": "📒 Мадам {name} купила тетрадку сплетен. Теперь есть о чём спросить.",
    "ball": "🔮 Мадам {name} купила настоящий хрустальный шар. Старый оказался плафоном.",
    "mirror": "🪞 Мадам {name} купила зеркало за спиной клиента. Врать лицом стало труднее.",
    "flyers": "📰 Мадам {name} купила объявления на подъездах. «Верну мужа, сниму порчу, оформлю вычет».",
    "neon": "💡 Мадам {name} купила неоновую вывеску «МАГИЯ». Буква Г уже мигает.",
    "reviews": "⭐ Мадам {name} купила отзывы. Двенадцать человек довольны, трое из них существуют.",
    "zina": "🚪 Мадам {name} купила тётю Зину. Скептиков теперь разворачивают прямо на лестнице.",
    "candles": "🕯 Мадам {name} купила свечи с запахом денег. Клиенты доверяют, ещё не сев.",
    "tea": "🫖 Мадам {name} купила чай с чабрецом. Сеанс нравится даже тем, кто не любит чабрец.",
    "armchair": "🛋 Мадам {name} купила кресло, из которого трудно встать. Вопросы теперь терпят: деваться некуда.",
    "cat": "🐈‍⬛ Мадам {name} купила чёрного кота. Кот пока не в курсе.",
})

METHOD_TITLES = MappingProxyType({"tarot": "Таро", "palm": "ладонь", "horoscope": "гороскоп", "energy": "энергетика"})

RANKS = (
    (12, "Храм всех религий сразу"),
    (8, "Академия тайных наук"),
    (4, "Салон на втором этаже"),
    (1, "Кабинет в подвале"),
    (0, "Каморка за гаражами"),
)


def owned_items(keys: Iterable[str]) -> tuple[Item, ...]:
    """Купленное в порядке витрины. Неизвестные ключи (старый файл) пропускаются."""
    wanted = set(keys)
    return tuple(item for item in ITEMS.values() if item.key in wanted)


def perks_of(keys: Iterable[str]) -> Perks:
    """Сложить эффекты купленного."""
    items = owned_items(keys)
    quality: dict[str, float] = {}
    for item in items:
        for method, bonus in item.perks.quality.items():
            quality[method] = quality.get(method, 0.0) + bonus
    skeptic = 1.0
    for item in items:
        skeptic *= item.perks.skeptic
    return Perks(
        fee=sum(item.perks.fee for item in items),
        clients=sum(item.perks.clients for item in items),
        questions=sum(item.perks.questions for item in items),
        readings=sum(item.perks.readings for item in items),
        quality=MappingProxyType(quality),
        accuracy=sum(item.perks.accuracy for item in items),
        deadline=max((item.perks.deadline for item in items), default=0.0),
        trust=sum(item.perks.trust for item in items),
        satisfaction=sum(item.perks.satisfaction for item in items),
        soft_questions=any(item.perks.soft_questions for item in items),
        skeptic=skeptic,
        forgive=sum(item.perks.forgive for item in items),
        rituals=tuple(line for item in items for line in item.perks.rituals),
    )


def rank(count: int) -> str:
    return next(title for threshold, title in RANKS if count >= threshold)


def describe(keys: Iterable[str]) -> str | None:
    """Строка о салоне для начала смены. Пока ничего не куплено, сказать нечего."""
    items = owned_items(keys)
    if not items:
        return None
    return f"🏠 {rank(len(items))}: {icons(keys)}"


def icons(keys: Iterable[str]) -> str:
    return " ".join(item.emoji for item in owned_items(keys))


def effects(keys: Iterable[str]) -> list[str]:
    """Что приносит всё купленное вместе, короткими фразами."""
    perks = perks_of(keys)
    result: list[str] = []
    if perks.fee:
        result.append(f"заработок +{round(perks.fee * 100)}%")
    if perks.clients:
        result.append(f"клиентов в смену +{perks.clients}")
    if perks.questions:
        result.append(f"вопросов +{perks.questions}")
    if perks.readings:
        result.append(f"гаданий +{perks.readings}")
    if perks.quality:
        result.append("точнее: " + ", ".join(title for method, title in METHOD_TITLES.items() if method in perks.quality))
    if perks.deadline:
        result.append("гороскоп называет срок")
    if perks.accuracy:
        result.append("реакции честнее")
    if perks.trust:
        result.append(f"доверие +{perks.trust}")
    if perks.satisfaction:
        result.append(f"довольство +{perks.satisfaction}")
    if perks.soft_questions:
        result.append("вопросы не портят настроение")
    if perks.skeptic < 1:
        result.append("скептиков вдвое меньше")
    if perks.forgive:
        result.append("кот прикрывает разоблачение")
    return result


def refusal(key: str, balance: int, keys: Iterable[str]) -> str | None:
    """Почему купить нельзя; ``None``, если можно."""
    item = ITEMS.get(key)
    if item is None:
        return "Такого в лавке нет."
    if key in set(keys):
        return f"{item.name} уже стоит в салоне."
    if balance < item.price:
        return f"Не хватает {item.price - max(balance, 0):,} ₽.".replace(",", " ")
    return None
