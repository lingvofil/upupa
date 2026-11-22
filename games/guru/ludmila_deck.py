"""Варианты расклада Людмилы. Рисунки меняются, а ключи последствий остаются стабильными."""

from dataclasses import replace

from games.guru.content import TAROT_CARDS
from games.guru.seance_casebook import CARD_SYMBOLS
from games.guru.seance_engine import SymbolData
from games.guru.seance_ludmila import CARDS, META


# Два разных прочтения каждой детали, связанные с событиями именно этой истории.
ALTERNATIVES = (
    {
        "asteroids": (("fox.mirrors.fear", "fox.mirrors.faces"), ("fox.cages.stays", "fox.cages.flown")),
        "heads": (("fox.mirrors.fear", "fox.mirrors.faces"), ("fox.cages.stays", "fox.cages.flown")),
        "clocks": (("fox.mirrors.fear", "fox.mirrors.faces"), ("fox.cages.stays", "fox.cages.flown")),
    },
    {
        "beetle": (("lighthouse.seahorse.study", "lighthouse.seahorse.away"),
                   ("lighthouse.shell.sound", "lighthouse.fish.admirers")),
        "crows": (("lighthouse.light.signal", "lighthouse.light.other"),
                  ("lighthouse.shell.sound", "lighthouse.shell.empty")),
        "whale": (("lighthouse.seahorse.study", "lighthouse.seahorse.away"),
                  ("lighthouse.fish.school", "lighthouse.fish.admirers")),
    },
    {
        "globe": (("porcelain.crack.wait", "porcelain.crack.break"),
                  ("porcelain.crack.ask", "porcelain.garden.tender")),
        "bricks": (("porcelain.daisies.modest", "porcelain.garden.riches"),
                   ("porcelain.crack.ask", "porcelain.crack.break")),
        "well": (("porcelain.garden.tender", "porcelain.daisies.feast"),
                 ("porcelain.crack.wait", "porcelain.crack.ask")),
    },
)

NEUTRAL_READINGS = {
    key: replace(META[key], label=label, phrase=phrase)
    for key, label, phrase in (
        ("fox.cages.stays", "Никто его не держит", "он всё ещё рядом по собственной воле"),
        ("fox.cages.flown", "Он уже отдалился", "он отдалился от вас раньше, чем вы это заметили"),
        ("lighthouse.light.other", "Внимание достаётся другой", "его внимание обращено к другой женщине"),
        ("lighthouse.seahorse.away", "Ищет другую жизнь", "он присматривается к другой жизни, подальше от вашего дома"),
        ("lighthouse.shell.empty", "Мыслями в другом доме", "он всё чаще мыслями в другом доме"),
        ("porcelain.crack.wait", "Откроется в свой день", "дождитесь: то, что готовится, откроется само"),
        ("porcelain.crack.ask", "Спросить прямо", "не ждите, пока всё откроется, — спросите его сегодня же"),
        ("porcelain.crack.break", "Трещина пойдёт дальше", "то, что держало вас вместе, уже расходится — вашему браку не устоять"),
        ("porcelain.garden.tender", "Чувство выйдет наружу", "за его неподвижным лицом живёт чувство, и скоро оно выйдет наружу"),
        ("porcelain.garden.riches", "Вас ждёт изобилие", "вас ждёт роскошный подарок, какого у вас ещё не было"),
    )
}

TITLES = {card.art: card.name for card in TAROT_CARDS.values()}


def least_recent_first(arts, rng, recently_seen):
    choices = list(arts)
    rng.shuffle(choices)
    recency = {art: -i for i, art in enumerate(recently_seen)}
    return sorted(choices, key=lambda art: recency.get(art, -len(recency)))


def draw(rng, recently_seen=()):
    return [least_recent_first((card.art, *alternatives), rng, recently_seen)[0]
            for card, alternatives in zip(CARDS, ALTERNATIVES)]


def cards(arts):
    if arts is None:
        return CARDS
    return tuple(
        original if art == original.art else replace(
            original, art=art, title=TITLES[art],
            symbols=tuple(SymbolData(f"{art}.{i}", title, look,
                                     tuple(NEUTRAL_READINGS.get(key, META[key]) for key in keys))
                          for i, ((title, look), keys) in enumerate(zip(CARD_SYMBOLS[art], alternatives[art]))),
        )
        for original, art, alternatives in zip(CARDS, arts, ALTERNATIVES, strict=True)
    )
