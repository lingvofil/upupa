"""Словарь, архетипы и тексты салона «Гуру» v0.2.

Всё здесь — данные: теги слотов конструктора, рамки методов гадания, реплики
архетипов, звания. Сюжетные тексты шести историй — в ``stories``.

Род у каждого архетипа фиксирован, поэтому реплики пишутся сразу в нужном роде.
Игрок для клиентов — «мадам» независимо от пола: это сценический образ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

SLOTS = ("who", "what", "when")
SLOT_TITLES = MappingProxyType({"who": "Кто", "what": "Что", "when": "Когда"})


# --- слоты конструктора -----------------------------------------------------

@dataclass(frozen=True)
class WhoTag:
    key: str
    label: str  # кнопка
    phrase: str  # в предсказании: «… {phrase} изменит»
    card: str  # карта Таро, которая на него указывает
    meaning: str  # толкование карты


@dataclass(frozen=True)
class WhatTag:
    key: str
    label: str
    sphere: str
    emotion: str
    third: str  # «… коллега изменит»
    second: str  # «… вы сами измените»


@dataclass(frozen=True)
class WhenTag:
    key: str
    label: str
    phrase: str  # «Мне кажется, {phrase} …»
    lead: str  # начало развязки: «В пятницу …»
    mark: str  # как срок выглядит в бытовой детали: «пятница»


WHO: Mapping[str, WhoTag] = MappingProxyType({tag.key: tag for tag in (
    WhoTag("colleague", "Коллега", "человек из рабочего окружения", "Королева мечей",
           "кто-то острый на язык — рядом, на работе"),
    WhoTag("partner", "Партнёр", "деловой партнёр", "Двойка пентаклей",
           "человек, с которым вас связывают общие деньги"),
    WhoTag("relative", "Родственник", "кто-то из родни", "Императрица",
           "кто-то из семьи, очень близкий"),
    WhoTag("ex", "Человек из прошлого", "человек из прошлого", "Шестёрка кубков",
           "тот, кто был раньше и так и не отпустил"),
    WhoTag("newbie", "Новое знакомство", "новый знакомый", "Паж жезлов",
           "кто-то новый, появился недавно и много обещает"),
    WhoTag("self", "Вы сами", "вы сами", "Отшельник",
           "ответ в вас самих, как ни банально"),
    WhoTag("animal", "Животное", "кто-то с хвостом", "Луна",
           "кто-то на четырёх лапах… или это метафора"),
    WhoTag("tax", "Налоговая", "налоговая", "Император",
           "государство в лице очень серьёзного человека"),
)})

WHAT: Mapping[str, WhatTag] = MappingProxyType({tag.key: tag for tag in (
    WhatTag("cheat", "Изменит", "love", "guilt", "изменит", "измените"),
    WhatTag("return", "Вернётся", "love", "hope", "вернётся", "вернётесь"),
    WhatTag("betray", "Предаст", "work", "anger", "предаст", "предадите"),
    WhatTag("offer", "Предложит должность", "work", "hope", "предложит новую должность", "получите новую должность"),
    WhatTag("lose", "Потеряет деньги", "money", "fear", "потеряет крупную сумму", "потеряете крупную сумму"),
    WhatTag("gain", "Принесёт деньги", "money", "hope", "принесёт деньги", "принесёте деньги"),
    WhatTag("leave", "Уйдёт", "family", "fear", "уйдёт из семьи", "уйдёте из семьи"),
    WhatTag("secret", "Раскроет тайну", "family", "guilt", "раскроет тайну", "раскроете тайну"),
)})

WHEN: Mapping[str, WhenTag] = MappingProxyType({tag.key: tag for tag in (
    WhenTag("today", "Сегодня", "сегодня же", "В тот же вечер", "сегодня, 19:00"),
    WhenTag("week", "До конца недели", "до конца недели", "В пятницу", "пятница"),
    WhenTag("month", "В течение месяца", "в течение месяца", "Через три недели", "30-е число"),
    WhenTag("sudden", "Когда не ждёте", "когда вы меньше всего будете ждать,", "Когда никто уже ничего не ждал,",
            "когда-нибудь"),
)})

SPHERES = ("love", "work", "money", "family")
SPHERE_TITLES = MappingProxyType({"love": "отношения", "work": "работа", "money": "деньги", "family": "семья"})

EMOTIONS = MappingProxyType({
    "guilt": "вина",
    "fear": "страх потери",
    "hope": "надежда",
    "anger": "злость",
})


# --- методы -----------------------------------------------------------------

@dataclass(frozen=True)
class PalmLine:
    key: str
    label: str
    sphere: str
    speaks: str
    silent: str


PALM_LINES: Mapping[str, PalmLine] = MappingProxyType({line.key: line for line in (
    PalmLine("heart", "❤️ Линия сердца", "love",
             "Линия сердца глубокая и рваная, как бывший. Дело в отношениях.",
             "Линия сердца ровная, как у монаха. Любовь тут ни при чём."),
    PalmLine("head", "🧠 Линия головы", "work",
             "Линия головы ломается посередине: всё упирается в работу.",
             "Линия головы скучная. Работа тут ни при чём."),
    PalmLine("fate", "💰 Линия судьбы", "money",
             "Линия судьбы раздваивается у основания: дело в деньгах.",
             "Линия судьбы спокойная. Деньги ни при чём."),
    PalmLine("life", "🏠 Линия жизни", "family",
             "Линия жизни петляет вокруг большого пальца: семья, дом, родня.",
             "Линия жизни прямая, как шпала. Семья ни при чём."),
)})
LINE_FOR_SPHERE = MappingProxyType({line.sphere: line.key for line in PALM_LINES.values()})

TAROT_DECOR = (
    "Шут", "Маг", "Колесо Фортуны", "Повешенный", "Башня", "Звезда", "Солнце",
    "Суд", "Мир", "Смерть", "Умеренность", "Дьявол", "Колесница",
)

ENERGY_CLEAR = MappingProxyType({
    "guilt": "Аура грязно-серая, как у человека с чужой перепиской в телефоне. Вина.",
    "fear": "Аура дрожит по краям. Страх что-то потерять.",
    "hope": "Аура тёплая и наивная. Надежда.",
    "anger": "Аура красная и искрит. Злость.",
})

ZODIAC_SIGNS = (
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы",
)

QUESTION_TEXTS = MappingProxyType({
    "who": "Карты должны знать, с чем работаем. Кто тут замешан?",
    "what": "Чего ты боишься больше всего? Что случится?",
    "when": "Когда, по-твоему, всё решится?",
})

CONFIDENCE = ("maybe", "see", "swear")
CONFIDENCE_LABELS = MappingProxyType({
    "maybe": "🤔 Мне кажется…",
    "see": "👁 Я отчётливо вижу…",
    "swear": "🔥 Клянусь!",
})
CONFIDENCE_PREFIX = MappingProxyType({
    "maybe": "Мне кажется,",
    "see": "Я отчётливо вижу:",
    "swear": "Клянусь своим третьим глазом:",
})

METHOD_LABELS = MappingProxyType({
    "palm": "🖐 Ладонь",
    "tarot": "🃏 Таро",
    "horoscope": "♈ Гороскоп",
    "energy": "🌀 Энергетика",
    "question": "❓ Спросить",
})
MAIN_METHODS = ("palm", "tarot", "horoscope")

SOURCE_LABELS = MappingProxyType({
    "disclosed": ("💬", "со слов клиента"),
    "question": ("❓", "ответ на вопрос"),
    "observation": ("👀", "видно сразу"),
    "palm": ("🖐", "ладонь"),
    "tarot": ("🃏", "Таро"),
    "horoscope": ("♈", "гороскоп"),
    "energy": ("🌀", "энергетика"),
})


# --- архетипы ---------------------------------------------------------------

@dataclass(frozen=True)
class Archetype:
    key: str
    title: str
    emoji: str
    base_trust: int
    style: str  # honest | overreact | poker — насколько реакции выдают правду
    echo: str  # expose | discount | delight — как он реагирует на свои же слова
    blurts: bool  # на вопрос выбалтывает ещё один слот
    suspicion: Mapping[str, int] | None  # только у скептика
    reactions: Mapping[str, tuple[str, ...]]  # warm | cold | neutral
    answers: Mapping[str, str]  # ответ на вопрос по слоту: {value}
    sign_answer: str
    echo_lines: Mapping[str, str]  # soft | critical
    outcomes: Mapping[str, str]  # hit | partial | miss | exposure
    suspicion_lines: tuple[tuple[int, str], ...] = field(default_factory=tuple)


SKEPTIC = Archetype(
    key="skeptic",
    title="Скептик",
    emoji="🧐",
    base_trust=20,
    style="poker",
    echo="expose",
    blurts=False,
    suspicion=MappingProxyType({"palm": 10, "tarot": 15, "horoscope": 20, "energy": 10, "question": 35}),
    reactions=MappingProxyType({
        "neutral": (
            "{name} невозмутимо что-то записывает.",
            "{name}: «Продолжайте». Лицо — кирпич.",
            "{name} смотрит на тебя как на экспонат.",
        ),
    }),
    answers=MappingProxyType({
        "who": "«Ну, допустим, {value}. Записываю, что гадалка задаёт вопросы»",
        "what": "«Допустим: {value}. Вам виднее, вы же ясновидящая»",
        "when": "«Допустим, {value}. Это тоже запишу»",
    }),
    sign_answer="«Я в это не верю. Ну, {sign}»",
    echo_lines=MappingProxyType({
        "soft": "{name}: «Вы повторяете за мной. Записываю»",
        "critical": "{name}: «Так я же вам это только что сам сказал!» Встаёт и выключает диктофон.",
    }),
    outcomes=MappingProxyType({
        "hit": "{name} роняет диктофон. Долго молчит. «…Как?»",
        "partial": "{name}: «Отчасти. Но пост я всё равно напишу»",
        "miss": "{name}: «Мимо. Ставлю галочку»",
        "exposure": "{name} разворачивает ноутбук: таблица всех твоих приёмов с таймкодами. Видео выйдет в 19:00.",
    }),
    suspicion_lines=((40, "{name} щурится."), (60, "{name} достаёт блокнот."), (80, "{name} включает диктофон.")),
)

JEALOUS = Archetype(
    key="jealous",
    title="Ревнивая",
    emoji="😤",
    base_trust=50,
    style="overreact",
    echo="delight",
    blurts=True,
    suspicion=None,
    reactions=MappingProxyType({
        "warm": (
            "{name}: «Я ТАК И ЗНАЛА!»",
            "{name} хватается за сердце: «Продолжайте, я готова!»",
            "{name} уже печатает подружкам: «девочки, она видит!!!»",
        ),
    }),
    answers=MappingProxyType({
        "who": "«Да {value}, кто ж ещё! Я сразу поняла!»",
        "what": "«Что {value}! Я же чувствую!»",
        "when": "«{value}, я уверена!»",
    }),
    sign_answer="«{sign}. А муж — Близнецы, двуличный знак, вы понимаете, да?»",
    echo_lines=MappingProxyType({
        "soft": "{name}: «Вот! Я же говорила!»",
        "critical": "{name}: «Вот! Я же говорила!»",
    }),
    outcomes=MappingProxyType({
        "hit": "{name}: «Я ТАК И ЗНАЛА! Девочки, вы не поверите!» Уже пишет в чат подружкам.",
        "partial": "{name}: «Ну… что-то в этом есть». Подозрительно косится на свой телефон.",
        "miss": "{name}: «Я пришла узнать про мужа, а вы мне что?!»",
        "exposure": "{name} устраивает грандиозный скандал. Не тому человеку. Весь район знает, кто ей нагадал.",
    }),
)

BUSINESSMAN = Archetype(
    key="businessman",
    title="Бизнесмен",
    emoji="💼",
    base_trust=35,
    style="honest",
    echo="discount",
    blurts=False,
    suspicion=None,
    reactions=MappingProxyType({
        "warm": ("{name} подаётся вперёд: «Так-так».", "{name} перестаёт смотреть на часы."),
        "cold": ("{name} морщится: «Мимо. Дальше».", "{name} смотрит на часы: «Я за воду не плачу»."),
    }),
    answers=MappingProxyType({
        "who": "«{value}. Дальше»",
        "what": "«Короче: {value}. Или нет. За это я вам и плачу»",
        "when": "«{value}. Время — деньги»",
    }),
    sign_answer="«{sign}. Давайте быстрее, у меня созвон»",
    echo_lines=MappingProxyType({
        "soft": "{name}: «Это я вам сам сказал. За что я плачу?»",
        "critical": "{name}: «Вы мне мои же слова продаёте под клятву?»",
    }),
    outcomes=MappingProxyType({
        "hit": "{name} снимает часы: «Сколько стоит, чтобы вы работали только на меня?»",
        "partial": "{name}: «Похоже на правду. Наполовину. Плачу наполовину»",
        "miss": "{name}: «Я плачу за конкретику, а не за поэзию»",
        "exposure": "{name}: «Вы мне гарантировали. Ждите юристов». Отзыв на картах уже висит.",
    }),
)

ARCHETYPES: Mapping[str, Archetype] = MappingProxyType({a.key: a for a in (SKEPTIC, JEALOUS, BUSINESSMAN)})


# --- служебные реплики ------------------------------------------------------

MERCURY_LINE = "♈ Ретроградный Меркурий сдвинул сроки — клиент не в претензии."
SWORN_PARTIAL_LINE = "Мадам клялась, а сбылось наполовину. Неловко."
STREAK_LINES = MappingProxyType({
    2: "🔥 По району ползёт слух: мадам видит насквозь.",
    3: "🔥🔥🔥 Третий глаз открыт!",
})
MASTERY_LINE = "🎓 Мастер всех эзотерических наук!"
LEGEND_LOST_LINE = "🕯 Легенда салона тает: осталось {left}."
LEGEND_LAST_LINE = "🕯 Ещё одно разоблачение — и салон закроют."
LEGEND_GONE_LINE = "🚨 Легенда салона кончилась. Салон разоблачён."
SUSPICION_EXIT_LINE = "{name} встаёт посреди сеанса: «Достаточно. Я всё записал»."

MOOD_BANDS = ((70, "смотрит с обожанием"), (45, "слушает"), (25, "сомневается"), (0, "косится на дверь"))
SUSPICION_BANDS = ((80, "включил диктофон"), (60, "записывает"), (40, "щурится"), (0, "спокоен"))


# --- итог -------------------------------------------------------------------

SCORE_TITLES = (
    (13, "Почётный академик Тибетской академии"),
    (9, "Великий провидец"),
    (5, "Магистр тёмных искусств"),
    (1, "Дипломированный шарлатан"),
)
LOW_SCORE_TITLE = "Ученик астрологии"
CROOK_TITLE = "Международный мошенник года"
EXPOSED_TITLE = "Звезда паблика «Разоблачаем шарлатанов»"
MASTERY_ACHIEVEMENT = "Мастер всех эзотерических наук"
STREAK_ACHIEVEMENT = "Третий глаз открыт"
