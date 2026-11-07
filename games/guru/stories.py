"""Шесть историй клиентов: по две на архетип — прямая и с подвохом.

В истории с подвохом жалоба и ответы клиента — его заблуждение. Правда «когда»
выбирается на партию из ``when_options``, чтобы историю нельзя было выучить.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from games.guru.model import Combo, Observation, Story

STORIES: tuple[Story, ...] = (
    # --- ревнивая -----------------------------------------------------------
    Story(
        id="jealous.lena",
        archetype="jealous",
        names=("Света", "Кристина", "Оксана"),
        complaint="Муж какой-то странный стал. Задерживается, телефон кладёт экраном вниз. Это кто-то с работы, я чувствую.",
        disclosed=MappingProxyType({"who": "colleague", "what": "cheat"}),
        observation=Observation(
            "when", ("@when",), "В её телефоне скриншот календаря мужа: «Корпоратив — {when_mark}»."
        ),
        who="colleague",
        what="cheat",
        when_options=("week", "today"),
        who_decoys=("ex", "newbie", "animal"),
        reveal="{when_lead} на корпоративе Лена из бухгалтерии перепутала чат и написала «котик, я уже еду» в общий канал отдела.",
        fee=2000,
        jackpot=Combo(
            (("who", "animal"), ("what", "secret")),
            "Его спалил кот: притащил с балкона серёжку. Такие во всём отделе носит одна Лена.",
        ),
    ),
    Story(
        id="jealous.bookie",
        archetype="jealous",
        names=("Наташа", "Ирина", "Марина"),
        complaint="Он прячет телефон и по ночам что-то шепчет. Точно баба. Наверняка бывшая объявилась.",
        disclosed=MappingProxyType({"who": "ex", "what": "cheat"}),
        observation=Observation(
            "sphere", ("money", "love"),
            "На ней новый браслет — подарок мужа «с премии», о которой она раньше не слышала.",
        ),
        who="newbie",
        what="lose",
        when_options=("today", "week"),
        who_decoys=("ex", "colleague", "animal"),
        reveal="{when_lead} «Спартак» проиграл, а вместе с ним — отпускные. «Бывшая» оказалась букмекером Олегом.",
        fee=2000,
        jackpot=Combo(
            (("who", "animal"), ("what", "secret")),
            "Тайну раскрыл попугай: в три часа ночи он заорал на всю квартиру «Ставка не сыграла! Олег, дай в долг!»",
        ),
    ),
    # --- бизнесмен ----------------------------------------------------------
    Story(
        id="businessman.partner",
        archetype="businessman",
        names=("Эдуард", "Руслан", "Артур"),
        complaint="Вкладываться мне в новый проект или нет? Партнёр торопит: сейчас или никогда.",
        disclosed=MappingProxyType({"who": "partner"}),
        observation=Observation("emotion", ("anger",), "О партнёре говорит сквозь зубы и хрустит костяшками."),
        who="partner",
        what="betray",
        when_options=("week", "month"),
        who_decoys=("colleague", "relative", "tax"),
        reveal="{when_lead} партнёр вывел все деньги на счёт в Дубае и прислал стикер «держись».",
        fee=5000,
        jackpot=Combo(
            (("who", "relative"), ("what", "gain")),
            "Тёща тайком вложилась в его проект через подставную фирму и вытащила его из ямы. "
            "Теперь она его партнёр. Это хуже.",
        ),
        trap=Combo(
            (("who", "tax"), ("what", "secret")),
            "Он бледнеет и молча уходит. Через час у салона машина с мигалкой: клиент сам был под проверкой "
            "и решил, что мадам его сдала.",
        ),
    ),
    Story(
        id="businessman.wife",
        archetype="businessman",
        names=("Геннадий", "Олег", "Вадим"),
        complaint="Короче. Есть фонд, обещают сорок процентов годовых. Вкладываться? Мне нужен чёткий ответ.",
        disclosed=MappingProxyType({"what": "gain"}),
        beliefs=MappingProxyType({"who": "partner"}),
        observation=Observation(
            "who", ("relative", "partner"),
            "Каждые пять минут смотрит в телефон: пропущенные от «Любимая» и «Сергей Фонд».",
        ),
        who="relative",
        what="leave",
        when_options=("sudden", "month"),
        who_decoys=("partner", "colleague", "animal"),
        reveal="{when_lead} жена ушла к инструктору по теннису. Фонд, кстати, тоже лопнул, но это он заметил позже.",
        fee=5000,
        jackpot=Combo(
            (("who", "animal"), ("what", "leave")),
            "Ушла не только жена — ушла и собака. Собака, говорят, ушла первой и всё знала.",
        ),
    ),
    # --- скептик ------------------------------------------------------------
    Story(
        id="skeptic.mom",
        archetype="skeptic",
        names=("Аркадий", "Глеб", "Вениамин"),
        complaint="Я веду паблик «Разоблачаем шарлатанов». Удивите меня.",
        disclosed=MappingProxyType({}),
        observation=Observation("who", ("relative",), "Телефон вибрирует без остановки: «Мама». Он сбрасывает не глядя."),
        who="relative",
        what="lose",
        when_options=("week", "month"),
        who_decoys=("ex", "colleague", "self"),
        reveal="{when_lead} мама отнесла гадалке сорок тысяч на снятие венца безбрачия. С сына.",
        fee=1500,
        jackpot=Combo(
            (("who", "self"), ("what", "lose")),
            "Он сам тайком отнёс гадалке зарплату — «для эксперимента». Эксперимент удался, деньги нет.",
        ),
        trap=Combo(
            (("who", "ex"), ("what", "return")),
            "Он белеет: его бывшая — та самая гадалка, которую он разоблачил в прошлом году. "
            "Через час она стоит в дверях салона: кто тут нагадал её возвращение?",
        ),
    ),
    Story(
        id="skeptic.believer",
        archetype="skeptic",
        names=("Денис", "Стас", "Олег"),
        complaint="Я тут по заданию редакции. Коллега попросил проверить ваш салон. Ничего личного.",
        disclosed=MappingProxyType({"who": "colleague"}),
        beliefs=MappingProxyType({"what": "betray"}),
        observation=Observation(
            "emotion", ("guilt", "fear"), "Слишком часто оглядывается на дверь и прикрывает рукой нагрудный карман."
        ),
        who="self",
        what="secret",
        when_options=("today", "week"),
        who_decoys=("colleague", "tax", "animal"),
        reveal="{when_lead} на стриме у него из кармана выпала натальная карта с пометкой «спросить про Венеру».",
        fee=1500,
        jackpot=Combo(
            (("who", "animal"), ("what", "secret")),
            "Его кот на стриме уронил с полки натальную карту хозяина. Сорок тысяч зрителей увидели асцендент в Раке.",
        ),
    ),
)

STORIES_BY_ID: Mapping[str, Story] = MappingProxyType({story.id: story for story in STORIES})


def stories_of(archetype: str) -> tuple[Story, ...]:
    return tuple(story for story in STORIES if story.archetype == archetype)
