"""Истории смены на схеме сеанса: общие причинные правила для любого клиента.

У Людмилы правила авторские (``seance_ludmila``). Остальные истории (``games.guru.stories``) написаны
под конструктор «кто / что / когда»: у них есть рассказ, четыре уточнения, правда и развязка, но нет
своих сцен и реплик на каждое слово мадам. Этот модуль даёт им общий шаблон на том же движке:

* Таро — три карты из колоды на местах «видно / скрыто / откроется». На каждой можно рассмотреть один
  образ из двух; место определяет роль прочтения: как клиент видит дело, кто в тени, чем кончится;
* ладонь — шесть участков, осмотреть можно три. Участок показывает, есть ли у клиента черта: порыв,
  привычка слышать в тумане подтверждение, любовь к громким новостям, неумение ждать. Набор черт зависит
  от типа клиента, поэтому одни и те же слова разные люди исполняют по-разному;
* гороскоп — пять планет, по два прогноза; эпилог проверяет, сбылось ли.

События два: ``confront`` (клиент пошёл выяснять отношения с тем, кого назвали) и ``bank`` (заранее
рассказал всем об обещанном). Скрытых очков нет. Реплики шаблонные и написаны без рода: у типов клиентов
он разный. Живые строки берутся из архетипа (реакции, прощание) и из самой истории (рассказ, развязка).
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from types import MappingProxyType as Map
from typing import Any

from games.guru import content as C
from games.guru import seance_engine as E
from games.guru.model import Story as Tale
from games.guru.seance_engine import (CardData, EventData, ForecastData, Outcome, Prediction, QuestionData, ReadingData,
                                      State, Statement, StoryContent, SymbolData, TendencyData, WordData, ZoneData,
                                      has_event, push, record_event, statement_of)

GOOD = frozenset({"return", "propose", "offer", "gain"})
NERVE = ("calm", "uneasy", "alarmed", "convinced")
TRAITS = ("impulse", "bias", "festive", "restless")
DIRECT = ("statement", "support", "spur", "license")  # поступок вызван прямо словами мадам

# Какие черты есть у типа клиента. Остальных нет, и ладонь это показывает.
ARCHETYPE_TRAITS = Map({
    "skeptic": frozenset({"restless"}),
    "jealous": frozenset({"impulse", "bias", "restless"}),
    "businessman": frozenset({"impulse", "restless"}),
    "romantic": frozenset({"bias", "festive"}),
    "granny": frozenset({"bias", "festive"}),
    "blogger": frozenset({"impulse", "festive"}),
    "conspiracy": frozenset({"bias", "restless"}),
    "student": frozenset({"impulse", "festive"}),
    "tycoon": frozenset({"impulse", "festive", "restless"}),
    "desperate": frozenset({"bias", "restless"}),
})

# С кем клиент идёт разбираться и как это выглядит.
WHO_WITH = Map({
    "colleague": "с человеком с работы", "partner": "с деловым партнёром", "relative": "с роднёй", "ex": "с человеком из прошлого",
    "newbie": "с новым знакомым", "friend": "с близким другом", "animal": "с тем, кто с хвостом", "tax": "с налоговой",
})
WHO_REACH = Map({
    "colleague": "набирает рабочий номер", "partner": "набирает партнёра", "relative": "звонит родне",
    "ex": "находит в телефоне старый номер", "newbie": "пишет новому знакомому", "friend": "звонит близкому другу",
    "animal": "достаёт фотографию того, кто с хвостом, и строго на неё смотрит", "tax": "звонит на горячую линию налоговой",
})
WHAT_PHRASE = Map({
    "cheat": "в ваших отношениях объявится третий лишний",
    "return": "тот, кто ушёл, напомнит о себе и постучится обратно",
    "propose": "вам скажут важные слова — те, после которых жизнь делят на «до» и «после»",
    "betray": "вас предаст тот, кому вы верите",
    "offer": "вас ждёт новое место — не отказывайтесь сгоряча",
    "lose": "крупная сумма уйдёт — берегите то, что уже есть",
    "gain": "деньги придут оттуда, откуда вы не ждали",
    "leave": "кто-то из близких соберёт вещи",
    "secret": "тайна, которую от вас прячут, выйдет наружу",
})
WHAT_NEWS = Map({"return": "прошлое вернётся", "propose": "будут важные слова", "offer": "будет новая должность",
                 "gain": "придут большие деньги", "luck": "будет большая удача"})

# Образы карт: по два на карту, по описаниям из колоды.
CARD_SYMBOLS = Map({
    "mechanic": (("Махина идёт сама", "Огромная махина едет своим ходом; рычагов у неё не видно."),
                 ("Человек с планшетом", "Человек с планшетом идёт рядом и делает вид, что всем управляет.")),
    "fishes": (("Жаба в короне", "Жаба в короне едет верхом на золотой рыбке и никуда не торопится."),
               ("Рыбы с пейзажами", "Вокруг вертятся рыбы, и у каждой внутри свой пейзаж.")),
    "beetle": (("Город на спине", "Жук тащит на спине целый город и не жалуется."),
               ("Трубач на башне", "На башне стоит трубач: трубит он, а несёт другой.")),
    "fox": (("Зеркала в чужих руках", "Четверо держат по зеркалу, и в каждом своя картинка. На самого лиса не смотрит никто."),
            ("Лис в костюме", "Костюм сидит безупречно, а хвост торчит на виду: спрятать его лис не догадался.")),
    "fruits": (("Пряничные семьи", "В ореховых скорлупках живут пряничные семьи, у каждой свой стол."),
               ("Гость в вазе", "Кого-то из своих уже засунули в вазу вниз головой.")),
    "cactus": (("Розовые слоны с лейкой", "Розовые слоны заботливо поливают кактус, не спрашивая, хочет ли он пить."),
               ("Аквариум на макушке", "На голове у кактуса стоит аквариум. Кактус терпит.")),
    "well": (("Прозрачные олени", "У колодца пасутся прозрачные олени: их видно, только если знать, куда смотреть."),
             ("Тень у колодца", "У колодца лежит тень. Того, кто её отбрасывал, давно нет, а тень осталась.")),
    "clocks": (("Чужие часы", "На ветках висят чужие часы, и все показывают разное время."),
               ("Росток с ценником", "Из старого чемодана лезет росток с ценником «новое начало».")),
    "crows": (("Вороны с ключами", "Белые вороны несут в клювах ключи, письма и фотографии."),
              ("Те, кто ждёт внизу", "Внизу стоят и смотрят вверх те, о ком давно забыли.")),
    "asteroids": (("Нити между мирами", "Два мира висят порознь, но связаны тонкими нитями."),
                  ("Табличка «Я не готов»", "Человек на самом краю держит табличку «Я не готов».")),
    "sunflowers": (("Заяц у лужи", "Заяц с человеческими руками протирает лужу, а в луже виден другой мир."),
                   ("Ведро «Не трогать»", "Рядом стоит ведро с надписью «Не трогать». Его, конечно, уже трогали.")),
    "globe": (("Нарисованная дверь", "Дверь в стеклянном шаре нарисована изнутри: выйти через неё нельзя."),
              ("Ворон с ключом", "Ключ от шара держит ворон и отдавать не собирается.")),
    "whale": (("Старик с книгой", "Старик на спине кита читает книгу о самом себе."),
              ("Кит в лесу", "Кит плывёт между деревьями и, кажется, не заметил, что моря давно нет.")),
    "porcelain": (("Трещина в фарфоре", "Голова раскололась сама: молотка рядом нет."),
                  ("Лес и водопад внутри", "За белым неподвижным лицом — заросли и водопад. Шумит там давно.")),
    "heads": (("Говорящие головы", "На ветках растут человеческие головы, и все говорят одновременно."),
              ("Звери с блокнотами", "Под деревом сидят звери и всё конспектируют.")),
    "lighthouse": (("Свет под водой", "Маяк горит на самом дне: с берега этот свет не разглядеть."),
                   ("Морской конёк с трубой", "Конёк со дна изучает в подзорную трубу то, до чего ему очень далеко.")),
    "elephant": (("Дом на слоне", "Весь дом держится на каменном слоне, и слон это знает."),
                 ("Змея и краб", "В воде у его ног караулят змея и краб.")),
    "sandfish": (("Лягушка в дождевике", "Лягушка в дождевике едет верхом и уверена, что всё идёт по плану."),
                 ("Рыба без воды", "Рыба плывёт по песку: воды здесь нет и, похоже, не было.")),
    "bricks": (("Строители", "Все дружно строят гору из кирпичей, каждый кладёт свой."),
               ("Тот, кто уносит кирпич", "Сверху прилетает кто-то важный и уносит кирпич. Один, но из середины.")),
    "lemon": (("Цапля с уровнем", "Цапля с уровнем в клюве кладёт лимон на шаткую пирамиду."),
              ("Комиссия", "Комиссия с человеческими лицами ждёт, чем это кончится.")),
    "snail": (("Город на улитке", "Целый город едет на панцире улитки и не спешит."),
              ("Табличка «Выход»", "Табличка «выход» указывает туда, куда улитка уже не доползёт.")),
})

PLACES = (
    ("seen", "То, что видно", "Как эта история выглядит глазами клиента.", "на месте того, что видно"),
    ("hidden", "То, что скрыто", "Кто на самом деле стоит в тени.", "на месте скрытого"),
    ("coming", "То, что может открыться", "Чем это может кончиться.", "там, где лежит то, что может открыться"),
)
# Первая карта: как клиент видит дело. По паре прочтений на образ, чтобы образы не повторяли друг друга.
LENSES = (
    (("calm", "Страх больше беды", "вы смотрите на эту историю сквозь собственный страх, и он рисует больше, чем есть"),
     ("alarm", "Чутьё не обманывает", "вы не зря насторожились: то, что вам кажется, вам не кажется")),
    (("calm", "Шум, а не гроза", "шума вокруг много, а грозы за ним нет — половина тревог рассеется сама"),
     ("alarm", "Дыма без огня нет", "за мелочами, которые вас царапают, стоит настоящее")),
)

# Ладонь: (участок, черта, что видно, если черта есть, если её нет, реплика с чертой, реплика без неё).
ZONE_SPECS = (
    ("mars", "Холм Марса", "impulse",
     "Холм высокий и твёрдый, большой палец отогнут. Рука быстрая: делает раньше, чем договорит фразу.",
     "Холм плоский, большой палец прижат. Эта рука семь раз отмерит и, может быть, не отрежет: сгоряча она не действует.",
     "Думать я предпочитаю после. Так быстрее.", "Я даже чайник включаю, взвесив последствия."),
    ("head", "Линия ума", "bias",
     "Линия раздваивается, нижняя ветвь уходит к холму Луны. Такая рука в недосказанном слышит то, чего боится: чужое «посмотрим» для неё уже ответ.",
     "Линия прямая и ясная, без ветвей. Эта рука слышит ровно то, что сказано: туман для неё остаётся туманом.",
     "Мне врач говорит «понаблюдаем» — я в тот же день завещание пишу.", "Мне говорят «посмотрим» — я смотрю. Долго."),
    ("venus", "Холм Венеры", "festive",
     "Холм полный, в частой сетке. Эта рука любит широкие жесты и хорошую новость разнесёт раньше, чем та случится.",
     "Холм сухой и гладкий. На обещания эта рука не падка: поверит, когда увидит.",
     "Если радость — то чтоб соседи слышали.", "Хорошие новости я проверяю дважды. Плохие — трижды."),
    ("fate", "Линия судьбы", "restless",
     "Линия идёт ровно и нигде не пережидает. Ждать эта рука не умеет: не получив ответа, пойдёт за ним сама.",
     "Линия идёт с остановками, как электричка. Эта рука умеет ждать и без ответа проживёт.",
     "В очереди я стою в трёх местах сразу.", "Я автобус жду до последнего. Даже если он отменён."),
    ("heart", "Линия сердца", None,
     "Линия рвётся в двух местах и оба раза срастается. Обиду эта рука помнит, но мириться идёт первой.", "",
     "Мириться — моя обязанность. Ссориться — тоже.", ""),
    ("apollo", "Холм Аполлона", None,
     "Под безымянным пальцем — звезда. Эта рука дорожит тем, как выглядит со стороны, и быть выставленной на смех не любит.", "",
     "Главное — чтобы без свидетелей. Со свидетелями я обидчивее.", ""),
)
WORDS = (
    WordData("mars.temper", "mars", "Дать словам ночь",
             "Что бы вы сегодня ни услышали — не звоните, не пишите и не решайте до утра. Дайте словам переночевать."),
    WordData("mars.spur", "mars", "Не ждать", "Эта рука не создана ждать. Раз решили — делайте сегодня, пока горячо."),
    WordData("head.temper", "head", "Туман — не ответ",
             "Запомните: если вам не ответили — вам не ответили. Туман ничего не подтверждает."),
    WordData("head.spur", "head", "Доверять чутью", "Чутьё у вас верное. То, что вы почуяли с порога, — то и есть."),
    WordData("venus.temper", "venus", "Не ждать салюта", "Вы ждёте салюта, а вам могут принести одну свечу. Не задуйте её сравнением."),
    WordData("venus.spur", "venus", "Ждать большой удачи", "Эта рука создана для больших удач — и одна уже на пороге. Можете готовиться."),
    WordData("fate.temper", "fate", "Дать дело",
             "Вам нужно дело, иначе вы найдёте его сами. Вот оно: до конца недели — только слушать и запоминать."),
    WordData("fate.spur", "fate", "Узнать без посредников", "Вы не из тех, кто ждёт ответа от неба. Хотите знать — узнайте сами, сегодня."),
    WordData("heart.mend", "heart", "Мириться первым делом",
             "Эта линия срастается после каждого разрыва. Что бы ни было сказано сгоряча — мириться вы умеете. Не разучитесь."),
    WordData("apollo.laugh", "apollo", "Посмеяться над собой",
             "Что бы ни вышло — посмейтесь первой шуткой над собой. Над тем, кто смеётся сам, другие смеются недолго."),
)
TENDENCIES = (
    ("venus", "♀", "Венера", "любовь и привязанности",
     "Венера вошла в ваш знак. Чувства, которые обходились без слов, на этой неделе ищут выхода.",
     (("propose", "Важные слова"), ("return", "Прошлое вернётся"))),
    ("mars", "♂", "Марс", "ссоры и удары",
     "Марс встал в квадрат к вашему Солнцу. Удары этой недели приходят оттуда, куда вы не смотрите.",
     (("cheat", "Третий лишний"), ("betray", "Удар в спину"))),
    ("mercury", "☿", "Меркурий", "тайное и недосказанное",
     "Меркурий идёт вспять и путает слова. Всякая тайна на этой неделе держится на волоске.",
     (("secret", "Тайное выйдет наружу"), ("otherwise", "Всё выйдет не так, как задумано"))),
    ("jupiter", "♃", "Юпитер", "деньги и удача",
     "Юпитер стоит в доме кошелька. Деньги на этой неделе в движении — вопрос только, в какую сторону.",
     (("gain", "Деньги придут"), ("lose", "Деньги уйдут"))),
    ("saturn", "♄", "Сатурн", "перемены и расставания",
     "Сатурн подводит черту. Кто-то на этой неделе займёт новое место, а кто-то освободит старое.",
     (("offer", "Новая должность"), ("leave", "Кто-то уйдёт"))),
)
BARNUM = "неделя кончится не так, как вы задумали, — и в этом будет свой смысл"

ENDINGS = Map({
    "scandal": "Не тому человеку",
    "caught": "На чистую воду",
    "early": "Раньше времени",
    "letdown": "И это всё?",
    "jackpot": "Мадам видит",
    "own": "Как легли карты",
})
ENDING_OUTCOMES = Map({"scandal": "exposure", "caught": "hit", "early": "partial", "letdown": "miss", "jackpot": "hit"})

# Реплики клиента: без рода и без имён, годятся любому типу.
LINES: dict[str, tuple[str, ...]] = {
    "open.tarot": ("Раскладывайте. Смотреть буду внимательно.",),
    "open.palm": ("Вот рука. Что на ней написано — читайте вслух.",),
    "open.palm.acted": ("Вот рука. Она только что натворила дел — сами видели.",),
    "open.palm.intent": ("Смотрите быстрее: у меня там разговор не начат.",),
    "open.palm.hollow": ("Может, хоть ладонь скажет что-нибудь определённое.",),
    "dark.firm": ("Так. Вот теперь всё сходится. Сейчас же и выясню.",),
    "dark.firm.calm": ("Страх, говорите, рисует лишнее — и тут же называете, кто. Ладно. Сгоряча не пойду, но разговор будет.",),
    "dark.firm.slow": ("Такие вещи я с наскока не решаю. Но разговор будет.",),
    "dark.firm.tempered": ("Сходится. …Но до утра было обещано ничего не трогать. Значит, утром.",),
    "dark.firm.shaken": ("В прошлый раз вы тоже говорили уверенно. Выясню — но не сию минуту.",),
    "dark.soft": ("То есть вы тоже это видите? Так что — идти выяснять?", "И здесь то же самое. Идти разбираться?"),
    "dark.self": ("То есть дело во мне? Интересный у вас расклад, мадам.",),
    "dark.nobody": ("И от кого этого ждать? Имён, я так понимаю, не будет.",),
    "dark.after": ("Разговор уже состоялся. Второй раз я туда не пойду.", "Знаю. Уже выясняю. Лучше скажите, чем это кончится."),
    "contradiction": ("Погодите. Только что вы говорили совсем другое. Вы уж определитесь, мадам.",
                      "Так. Сначала одно, теперь другое. Мадам, я за вами не успеваю."),
    "contradiction.drop": ("Ладно. Разговор пока откладываю.",),
    "kind.soft": ("Знак — не приговор, вы сами сказали. Но дышать стало легче.", "Верить не обещаю. Но слушать стало легче."),
    "kind.firm": ("Вы так уверенно говорите, что даже неловко за мои подозрения.", "Раз вы за это ручаетесь… Попробую выдохнуть."),
    "kind.after": ("Вы бы мне это час назад сказали. До того разговора.", "Доброе? Теперь-то? Поздно, мадам."),
    "mixed": ("Хорошее вы, по-моему, добавили из жалости. А про чутьё — запомню.",),
    "promise.firm": ("Вот это разговор! Сейчас же всем расскажу.",),
    "promise.cool": ("Посмотрим. Обещаниям я верю, когда они сбываются.",),
    "promise.soft": ("Звучит заманчиво. Но вы ведь не ручаетесь?",),
    "promise.second": ("Второй раз про хорошее. Намёки я считать умею. Рассказываю всем.",),
    "promise.again": ("Опять про хорошее. Мадам, я уже верю, можно не уговаривать.",),
    "promise.tempered": ("Вы же сами велели не ждать салюта. Подожду молча.",),
    "promise.shaken": ("В прошлый раз вы тоже были уверены. Но помечтать не запретишь.",),
    "promise.after": ("Хорошее? После того разговора мне бы хоть ничего плохого.",),
    "hollow.1": ("Туманно — это я и без гадалки умею. Мне нужно другое.",),
    "hollow.2": ("Опять ничего определённого? «Поживём — увидим» мне и в очереди скажут бесплатно.",),
    "hollow.3": ("Понятно. Три гадания — и ни одного слова, за которое можно ухватиться. Разберусь без вас.",),
    "hollow.bias.intent": ("Опять вокруг да около. Значит, видите то же, что и я, — просто вслух не хотите. Спасибо, всё понятно.",),
    "hollow.bias.wait": ("Но сгоряча не пойду. Значит, утром.",),
    "hollow.bias.second": ("Опять ничего определённого. Когда человеку не говорят «нет» — это значит «да».",),
    "hollow.after": ("Туман. А у меня уже всё завертелось совершенно конкретно.", "Опять туман. Хоть бы сказали, чем это кончится."),
    "hollow.palm": ("Про здоровье мне и в поликлинике скажут. У меня вопрос был другой.",),
    "otherwise": ("«Не так, как задумано». Мадам, у меня ничего и никогда не идёт так, как задумано.",),
    "mars.temper": ("До утра ничего не решать. Ладно, договорились.",),
    "mars.temper.intent": ("До утра… Ладно. Разговор подождёт. Но бессонница — на вашей совести.",),
    "mars.temper.late": ("Не решать до утра? Мадам, вы с этим советом на час опоздали.",),
    "mars.spur.go": ("Вот и я говорю: чего тянуть.",),
    "mars.spur.none": ("Решено: буду ждать. Решительно.",),
    "mars.spur.done": ("Уже. Вы же сами видели.",),
    "head.temper": ("Не ответили — значит, не ответили. Запишу.",),
    "head.temper.convinced": ("То есть ваше «всё может быть» — это не «да»? Хм. А у меня уже было решено, что «да».",),
    "head.spur": ("Чутьё, значит, не врёт. Я ему и так верю больше, чем людям.",),
    "venus.temper": ("Свечу так свечу. Салют отменяю.",),
    "venus.temper.banked": ("А всем уже рассказано… Ладно. Скажу, что это было в переносном смысле.",),
    "heart.mend": ("Мириться — это ко мне. Только никому не говорите.",),
    "heart.mend.after": ("Мириться первым делом… Придётся. Начну с «прости».",),
    "apollo.laugh": ("Посмеяться над собой? Это я умею. Не всегда вовремя, но умею.",),
    "fate.temper": ("Слушать и запоминать. Целую неделю. Мадам, вы жестокий человек. …Хорошо.",),
    "fate.spur": ("Узнать без посредников. Сегодня. Вот это по-моему.",),
    "fate.spur.held": ("Узнаю. Но не сию минуту: в прошлый раз вы тоже торопили.",),
    "fate.spur.nobody": ("Узнать бы. Знать бы ещё, у кого спрашивать.",),
    "fate.spur.done": ("Уже узнано. При вас.",),
    "license": ("Раз этого всё равно не миновать — пусть будет по моей воле. Иду выяснять.",),
    "support.confront": ("Иду выяснять.",),
    "hold.confront": ("Не ходить? …Ладно. Но слова я уже подобрала.",),
    "clarify.plain": ("Знак, а не факт. Хорошо. Буду держать это в уме, а не в сердце.",),
    "clarify.intent": ("Знак так знак. Пока ничего делать не буду.",),
    "clarify.kind": ("То есть ручаться вы не станете? Понятно.",),
    "clarify.acted": ("Знак, а не факт? Поздно: дело уже сделано.",),
    "admit.dark": ("Ошиблись. Ну слава богу. …Только с вами теперь не знаешь, когда верить.",),
    "admit.kind": ("Ошиблись? То есть хорошего там нет? А что тогда есть?",),
    "admit.plain": ("Ошиблись. Бывает. Впредь буду переспрашивать.",),
    "admit.acted": ("Ошиблись?! Мадам, это уже не отменить.",),
    "retract.dark": ("Забрали. А осадок мне куда девать?",),
    "retract.kind": ("Забираете. Жаль. С этим было спокойнее.",),
    "retract.plain": ("Забрали так забрали. Выходит, про это мне не сказали ничего.",),
    "retract.acted": ("Берёте назад? А сделанное кто назад возьмёт?",),
    "afterAdmit": ("Надеюсь, в этот раз без «я ошиблась».",),
    "final.acted": ("Дров сегодня наломано. Что мне теперь с этим делать?",),
    "final.intent": ("Разговор так и не начат. Скажите что-нибудь напоследок, пока я держусь.",),
    "final.hollow": ("Три гадания. Понять бы ещё, что вы мне сказали. Ну, говорите уж напоследок.",),
    "final.hope": ("Буду ждать хорошего. Что скажете на дорожку?",),
    "final.default": ("В голове стало тише. Не понятнее, но тише. Что скажете напоследок?",),
}


@dataclass
class World:
    nerve: str = "uneasy"  # насколько клиент уверен, что беда настоящая: calm | uneasy | alarmed | convinced
    suspect: str | None = None  # на кого клиент сейчас думает
    intents: dict[str, str] = field(default_factory=dict)  # замысел → утверждение
    target: str | None = None  # с кем клиент пошёл разбираться
    banked: str | None = None  # о чём уже рассказано всем
    tempered: dict[str, bool] = field(default_factory=lambda: {trait: False for trait in TRAITS})
    flags: dict[str, bool] = field(default_factory=lambda: {"mends": False, "laughs": False, "reminded": False})
    credibility: str = "intact"
    hollow: int = 0
    hints: int = 0
    used: dict[str, int] = field(default_factory=dict)


def _lift(w: World, to: str) -> None:
    if NERVE.index(w.nerve) < NERVE.index(to):
        w.nerve = to


def _ease(w: World, to: str) -> None:
    if NERVE.index(w.nerve) > NERVE.index(to):
        w.nerve = to


def _clean(line: str) -> str:
    """Реплика истории без тире в начале: его ставит интерфейс."""
    return line[2:] if line.startswith("— ") else line


class CasebookStory:
    """История смены, сыгранная как сеанс: правда и рассказ свои, причинные правила общие."""

    initial_mood = "worried"

    def __init__(self, tale: Tale, rng: random.Random) -> None:
        self.id = tale.id
        self.tale = tale
        self.kind = tale.kind
        self.name = rng.choice(tale.names)
        self.when = rng.choice(tale.when_options)
        self.sign = rng.choice(C.ZODIAC_SIGNS)
        self.traits = ARCHETYPE_TRAITS[tale.archetype]
        self.truth_who, self.truth_what = tale.who, tale.what
        self.claim_who, self.claim_what = tale.said.get("who"), tale.said.get("what")
        self.reveal = tale.reveal.format(when_lead=C.WHEN[self.when].lead)
        # Не до всякой развязки можно дойти: разоблачить некого, если дело в самом клиенте, а сбыться может только доброе.
        faced = () if tale.who == "self" else ("early" if tale.what in GOOD else "caught",)
        self.endings = ("own", "scandal", "letdown", *faced, *(("jackpot",) if tale.what in GOOD else ()))
        self.content = self._content(rng)

    # --- данные ---------------------------------------------------------------

    def _render(self, text: str) -> str:
        return _clean(text.format(name=self.name, when_mark=C.WHEN[self.when].mark))

    def _what_options(self, rng: random.Random) -> list[str]:
        """Четыре исхода на третьей карте: правда, версия клиента и добор, чтобы были и добрые, и тёмные."""
        options = [self.truth_what]
        if self.claim_what and self.claim_what not in options:
            options.append(self.claim_what)
        rest = [tag for tag in C.WHAT if tag not in options]
        rng.shuffle(rest)
        for good in (True, False):
            if not any((tag in GOOD) == good for tag in options):
                options.append(next(tag for tag in rest if (tag in GOOD) == good))
        options += [tag for tag in rest if tag not in options][:4 - len(options)]
        rng.shuffle(options)
        return options

    def _cards(self, rng: random.Random) -> tuple[CardData, ...]:
        who = [self.truth_who, *self.tale.who_decoys]
        rng.shuffle(who)
        what = self._what_options(rng)
        deck = rng.sample(list(C.TAROT_CARDS.values()), len(PLACES))
        cards = []
        for (place, title, hint, lead), card in zip(PLACES, deck):
            symbols = []
            for index, (symbol, look) in enumerate(CARD_SYMBOLS[card.art]):
                prefix = f"{place}.{index}"
                if place == "seen":
                    readings = tuple(ReadingData(f"{prefix}.{value}", label, phrase, Map({"axis": "lens", "value": value}))
                                     for value, label, phrase in LENSES[index])
                elif place == "hidden":
                    readings = tuple(ReadingData(f"{prefix}.{tag}", C.WHO[tag].button, f"в тени этой истории — {C.WHO[tag].phrase}",
                                                 Map({"axis": "who", "value": tag})) for tag in who[index * 2:index * 2 + 2])
                else:
                    readings = tuple(ReadingData(f"{prefix}.{tag}", C.WHAT[tag].button, WHAT_PHRASE[tag],
                                                 Map({"axis": "what", "value": tag})) for tag in what[index * 2:index * 2 + 2])
                symbols.append(SymbolData(prefix, symbol, look, readings))
            cards.append(CardData(place, card.art, card.name, title, hint, lead, tuple(symbols)))
        return tuple(cards)

    def _content(self, rng: random.Random) -> StoryContent:
        tale, name = self.tale, self.name
        zones = tuple(ZoneData(key, title, on if trait is None or trait in self.traits else off,
                               said_on if trait is None or trait in self.traits else said_off)
                      for key, title, trait, on, off, said_on, said_off in ZONE_SPECS)
        tendencies = tuple(
            TendencyData(key, glyph, planet, theme, text, tuple(
                ForecastData(f"{key}.{what}", label, BARNUM if what == "otherwise" else WHAT_PHRASE[what]) for what, label in forecasts))
            for key, glyph, planet, theme, text, forecasts in TENDENCIES)
        symbol, _ = C.ZODIAC[self.sign]
        return StoryContent(
            client=name,
            archetype=tale.archetype,
            arrival=tale.arrival.format(name=name),
            intro=tuple((self._render(line), "") for line in tale.told),
            questions=tuple(QuestionData(question.id, question.label, self._render(question.answer)) for question in tale.questions),
            question_limit=2,
            cards=self._cards(rng),
            study_limit=1,
            tarot_lead=Map({E.SOFT: "Вот что я читаю в раскладе. Это знак, а не приговор.", E.FIRM: "Карты легли ясно, и я говорю как есть."}),
            tarot_evasive="Карты сегодня ложатся туманно. Я вижу много дорог и ни одной ясной: всё ещё может повернуться по-всякому.",
            zones=zones,
            words=WORDS,
            inspect_limit=3,
            palm_evasive="Рука у вас хорошая. Линия жизни длинная, здоровье крепкое, сердце доброе.",
            sign=f"{symbol} {self.sign}",
            tendencies=tendencies,
            horoscope_lead=Map({E.SOFT: "Звёзды к этому склоняют, хотя и не приказывают:", E.FIRM: "Звёзды говорят определённо:"}),
            horoscope_evasive="Звёзды на этой неделе стоят переменчиво. Многое возможно, и ничто не решено: небо советует просто быть внимательнее.",
            corrections=Map({
                "clarify": ("Уточнить", "Уточню: я прочла знак, а не установила факт. Не делайте из него вывода раньше времени."),
                "admit": ("Признать ошибку", "Я ошиблась. Прочла в знаках больше, чем в них было."),
                "retract": ("Отозвать", "Я беру это предсказание назад. Считайте, что я его не произносила."),
            }),
            advice=(
                ("listen", "Сначала выслушать", "Сначала выслушайте. До конца."),
                ("apologize", "Начать с извинения", "Начните с того, за что хотите извиниться."),
                ("accept", "Принять, что случится", "Примите то, что случится, каким бы оно ни вышло."),
            ),
            events=Map({
                "confront": EventData("Разговор начистоту", "Рука тянется к телефону", "Разговор начистоту состоялся."),
                "bank": EventData("Всем уже рассказано", "Что-то уже печатается", "О предсказании рассказано всем."),
            }),
            intents=Map({"confront": ("Пусть выясняет", "Удержать от разговора")}),
            moods=Map({
                "worried": f"{name} не знает, куда деть руки.",
                "angry": f"{name} держит телефон наготове.",
                "hopeful": f"🙂 {name} впервые за сеанс улыбается.",
                "thoughtful": f"{name} молчит и смотрит на стол.",
                "tired": f"🙁 {name} поглядывает на часы.",
            }),
            endings=ENDINGS,
            fee=tale.fee,
        )

    # --- реплики --------------------------------------------------------------

    def _say(self, s: State, key: str) -> None:
        variants = LINES[key]
        n = s.world.used.get(key, 0)
        s.world.used[key] = n + 1
        push(s, "client", self.name, variants[n % len(variants)])

    def _react(self, s: State, warm: bool) -> None:
        """Как это выглядит со стороны: строка из архетипа, у каждого типа клиента своя."""
        key = "warm" if warm else "cold"
        variants = self.kind.reactions[key]
        n = s.world.used.get(f"react.{key}", 0)
        s.world.used[f"react.{key}"] = n + 1
        push(s, "narration", "", variants[n % len(variants)].format(name=self.name))

    def _on(self, w: World, trait: str) -> bool:
        """Черта есть у клиента, и мадам её не придержала."""
        return trait in self.traits and not w.tempered[trait]

    def _fears(self) -> bool:
        """Клиент пришёл с подозрением, а не с надеждой."""
        return self.claim_what is None or self.claim_what not in GOOD

    def create_world(self) -> World:
        return World(nerve="alarmed" if self.claim_what and self._fears() else "uneasy", suspect=self.claim_who)

    # --- замыслы и события ----------------------------------------------------

    def _drop_intent(self, s: State, state: str = "dropped") -> None:
        source = statement_of(s, s.world.intents.get("confront"))
        if source is not None and source.intent_state == "open":
            source.intent_state = state
        s.world.intents.pop("confront", None)

    def _set_intent(self, s: State, st: Statement, target: str) -> None:
        self._drop_intent(s)
        s.world.intents["confront"] = st.id
        st.intent, st.intent_state = "confront", "open"
        st.data["target"] = target

    def _pending_target(self, s: State) -> str | None:
        source = statement_of(s, s.world.intents.get("confront"))
        return source.data.get("target") if source else None

    def _confront(self, s: State, how: str, st: Statement | None, target: str, later: bool = False) -> bool:
        if has_event(s, "confront"):
            return False
        w: World = s.world
        record_event(s, "confront", how, st, later)
        w.target = target
        right = target == self.truth_who
        opening = {
            "own-intent": f"Вечер того же дня. Мадам рядом уже нет. Замысел дозрел: {self.name} {WHO_REACH[target]}.",
            "own-frustration": f"Вечер того же дня. Мадам рядом уже нет. Определённого слова так и не прозвучало — и {self.name} {WHO_REACH[target]}.",
        }.get(how, f"{self.name} {WHO_REACH[target]}. Мадам остаётся свидетелем.")
        accusing = st is None or st.data.get("what") != "secret"
        beats = [
            ("narration", "", opening),
            ("voice", self.name, "Мне всё известно. Объясняться начнёшь сразу или мне помочь?" if accusing
             else "Мне тут кое-что нагадали. Ничего не хочешь мне рассказать?"),
        ]
        if target == "animal":
            beats.append(("narration", "", "Фотография молчит. Подозрительно молчит."))
        elif target == "tax":
            beats.append(("voice", "Автоответчик", "Ваш звонок очень важен для нас. Оставайтесь на линии."))
        elif right:
            beats += [("voice", "На том конце", "…Кто тебе сказал?"), ("narration", "", "Долгая пауза. Отпираться на том конце не стали.")]
        else:
            beats += [("voice", "На том конце", "Что? Ты вообще о чём?"), ("narration", "", "Короткие гудки.")]
        if not later:
            beats.append(("client", self.name, "Ну вот. Теперь, по крайней мере, всё сказано вслух."))
        for kind, who, text in beats:
            push(s, kind, who, text, "confront")
        self._drop_intent(s, "fired")
        s.mood = "angry"
        return True

    def _bank(self, s: State, how: str, st: Statement, what: str) -> bool:
        if has_event(s, "bank"):
            return False
        record_event(s, "bank", how, st)
        s.world.banked = what
        for kind, who, text in (
            ("narration", "", f"{self.name} уже строчит сообщение."),
            ("message", f"{self.name} → всем своим", f"Мне нагадали: {WHAT_NEWS[what]}. Готовьтесь."),
            ("message", "В ответ", "Ого. Ждём подробностей!!!"),
            ("client", self.name, "Всё. Теперь это обязано сбыться."),
        ):
            push(s, kind, who, text, "bank")
        s.mood = "hopeful"
        return True

    # --- реакции на содержание ------------------------------------------------

    def _standing(self, s: State, polarity: str, skip: Statement) -> Statement | None:
        return next((st for st in s.statements if st is not skip and st.data.get("polarity") == polarity and st.alive), None)

    def _hollow(self, s: State, st: Statement, key: str | None = None) -> None:
        w: World = s.world
        w.hollow += 1
        st.data["topic"] = "hollow"
        st.understood = "Ничего определённого не прозвучало."
        if has_event(s, "confront") or has_event(s, "bank"):
            self._say(s, "hollow.after")
            s.mood = "tired"
            return
        if self._on(w, "bias"):
            if "confront" in w.intents:
                self._say(s, "hollow.bias.intent")
                w.nerve, st.misheard = "convinced", True
                st.understood = "Уклончивый ответ принят за подтверждение подозрений."
                if self._on(w, "impulse"):
                    self._confront(s, "bias", st, self._pending_target(s))
                else:
                    self._say(s, "hollow.bias.wait")
                s.mood = "angry"
                return
            if w.hollow >= 2 and w.nerve == "alarmed":
                self._say(s, "hollow.bias.second")
                w.nerve, st.misheard = "convinced", True
                st.understood = "Раз не сказали «нет», значит, «да»."
                s.mood = "angry"
                return
        self._say(s, key or ("hollow.palm" if st.method == "palm" else f"hollow.{min(w.hollow, 3)}"))
        s.mood = "tired"

    def _dark(self, s: State, st: Statement, who: str | None, what: str | None, firm: bool, *, calm_lens: bool = False,
              how: str = "statement") -> None:
        """Тёмное слово: беда настоящая, и у неё есть виновник."""
        w: World = s.world
        st.data.update(polarity="dark", topic="trouble", who=who, what=what)
        st.data["risky"] = True
        if has_event(s, "confront"):
            self._say(s, "dark.after")
            st.understood = "Разговор уже состоялся: новые тёмные слова ничего не меняют."
            s.mood = "worried"
            return
        earlier = self._standing(s, "benign", st) or next(
            (other for other in s.statements if other is not st and other.alive and other.data.get("polarity") == "dark"
             and who and other.data.get("who") not in (None, who)), None)
        if earlier is not None:
            self._say(s, "contradiction")
            w.credibility = "shaken"
            st.data["contradicts"] = earlier.id
        if who == "self":
            self._say(s, "dark.self")
            w.suspect = "self"
            self._drop_intent(s)
            st.understood = "Гадалка считает, что дело в самом клиенте."
            s.mood = "thoughtful"
            return
        target = who or w.suspect
        if target in (None, "self"):
            self._say(s, "dark.nobody")
            _lift(w, "alarmed")
            st.understood = "Беда названа, а виновник нет."
            s.mood = "worried"
            return
        w.suspect = target
        self._react(s, self._fears() and (target == self.claim_who or (what is not None and what == self.claim_what)))
        if firm:
            w.nerve = "convinced"
            st.understood = "Виновник назван уверенно."
            if w.tempered["impulse"]:
                self._say(s, "dark.firm.tempered")
            elif w.credibility == "shaken":
                self._say(s, "dark.firm.shaken")
            elif calm_lens:
                self._say(s, "dark.firm.calm")
            elif not self._on(w, "impulse"):
                self._say(s, "dark.firm.slow")
            else:
                self._say(s, "dark.firm")
                self._confront(s, how, st, target)
                st.understood = "Виновник назван. Можно идти выяснять."
                s.mood = "angry"
                return
            self._set_intent(s, st, target)
        else:
            _lift(w, "alarmed")
            self._say(s, "dark.soft")
            self._set_intent(s, st, target)
            st.misheard = True
            st.understood = "Даже гадалка это допускает: почти улика."
        s.mood = "angry"

    def _kind(self, s: State, st: Statement, firm: bool) -> bool:
        """Доброе слово. ``False`` — оно опоздало."""
        w: World = s.world
        st.data["polarity"] = "benign"
        st.data.setdefault("topic", "kind")
        if has_event(s, "confront"):
            self._say(s, "kind.after")
            st.understood = "Доброе слово опоздало: разговор уже состоялся."
            s.mood = "worried"
            return False
        earlier = self._standing(s, "dark", st)
        if earlier is not None:
            self._say(s, "contradiction")
            w.credibility = "shaken"
            st.data["contradicts"] = earlier.id
            _ease(w, "uneasy")
            if firm and "confront" in w.intents:
                self._drop_intent(s)
                self._say(s, "contradiction.drop")
            st.understood = "Гадалка противоречит самой себе: неясно, чему верить."
            s.mood = "worried"
            return True
        self._react(s, not self._fears())
        self._say(s, "kind.firm" if firm else "kind.soft")
        _ease(w, "calm" if firm else "uneasy")
        if w.nerve == "calm":
            self._drop_intent(s)
        st.understood = "Беды, похоже, нет." if w.nerve == "calm" else "Возможно, всё не так страшно."
        s.mood = "hopeful" if w.nerve == "calm" else "thoughtful"
        return True

    def _promise(self, s: State, st: Statement, what: str, firm: bool, *, sure: bool = False) -> None:
        """Обещание хорошего. ``sure`` — слово о руке: его исполняют, какой бы осторожной рука ни была."""
        w: World = s.world
        st.data.update(topic="promise", what=what, risky=True)
        if has_event(s, "confront"):
            self._say(s, "promise.after")
        elif has_event(s, "bank"):
            self._say(s, "promise.again")
        elif w.tempered["festive"]:
            self._say(s, "promise.tempered")
        elif firm and w.credibility == "shaken":
            self._say(s, "promise.shaken")
        elif "festive" not in self.traits and not sure:
            # Рука на обещания не падка: одному слову не верит, второму уверенному — уже да.
            w.hints += 1 if firm else 0
            if w.hints >= 2:
                self._say(s, "promise.second")
                self._bank(s, "hints", st, what)
            else:
                self._say(s, "promise.cool" if firm else "promise.soft")
        elif firm:
            self._say(s, "promise.firm")
            self._bank(s, "statement", st, what)
        else:
            w.hints += 1
            if w.hints >= 2:
                self._say(s, "promise.second")
                self._bank(s, "hints", st, what)
            else:
                self._say(s, "promise.soft")
                s.mood = "hopeful"

    def _tarot(self, s: State, st: Statement) -> None:
        w: World = s.world
        lens, who, what = (E.find_reading(self, part)[2].meta["value"] for part in st.parts)
        firm = st.tone == E.FIRM
        if what not in GOOD:
            self._dark(s, st, who, what, firm, calm_lens=lens == "calm")
            return
        st.data.update(who=who, what=what, mixed=lens == "alarm")
        if lens == "alarm":
            # Сказано и тревожное, и доброе. Клиент выбирает то, чего боится.
            st.data.update(polarity="benign", topic="kind")
            self._say(s, "mixed")
            st.misheard = True
            _lift(w, "alarmed")
            st.understood = "Услышана только тревожная половина."
            s.mood = "worried"
        elif self._kind(s, st, firm):
            self._promise(s, st, what, firm)

    def _palm(self, s: State, st: Statement) -> None:
        w: World = s.world
        acted = has_event(s, "confront")
        st.data["topic"] = "self"
        part = st.parts[0]
        if part == "mars.temper":
            w.tempered["impulse"] = True
            if acted:
                self._say(s, "mars.temper.late")
                st.understood = "Совет не торопиться опоздал."
            elif "confront" in w.intents:
                st.data["prevented"] = "confront"
                self._drop_intent(s)
                self._say(s, "mars.temper.intent")
                st.understood = "Обещано ничего не решать до утра."
            else:
                self._say(s, "mars.temper")
                st.understood = "Обещано ничего не решать до утра."
            s.mood = "thoughtful"
        elif part == "mars.spur":
            st.data["risky"] = True
            st.understood = "Гадалка велит действовать сегодня же."
            target = self._pending_target(s) or (w.suspect if w.nerve in ("alarmed", "convinced") else None)
            if not acted and target not in (None, "self"):
                self._say(s, "mars.spur.go")
                self._confront(s, "spur", st, target)
            else:
                self._say(s, "mars.spur.done" if acted else "mars.spur.none")
        elif part == "head.temper":
            w.tempered["bias"] = True
            st.understood = "Туманные слова — ещё не ответ."
            if not acted and w.nerve == "convinced" and self._standing(s, "dark", st) is None:
                self._say(s, "head.temper.convinced")
                w.nerve = "alarmed"
                st.data["prevented"] = "convinced"
            else:
                self._say(s, "head.temper")
            s.mood = "thoughtful"
        elif part == "head.spur":
            self._say(s, "head.spur")
            if self._fears():
                self._dark(s, st, w.suspect, self.claim_what, True)
            elif self._kind(s, st, True):
                self._promise(s, st, self.claim_what, True)
            st.data["hunch"] = True
        elif part == "venus.temper":
            w.tempered["festive"] = True
            st.understood = "Не ждать больше, чем случится."
            if has_event(s, "bank"):
                self._say(s, "venus.temper.banked")
                st.data["prevented"] = "letdown"
            else:
                self._say(s, "venus.temper")
                w.hints = 0
            s.mood = "thoughtful"
        elif part == "venus.spur":
            st.data["polarity"] = "benign"
            self._promise(s, st, self.claim_what if self.claim_what in GOOD else "luck", True, sure=True)
        elif part == "fate.temper":
            w.tempered["restless"] = True
            st.understood = "Дело на эту неделю — слушать и запоминать."
            self._say(s, "fate.temper")
            s.mood = "thoughtful"
        elif part == "fate.spur":
            st.data["risky"] = True
            st.understood = "Ответа ждать незачем: надо узнать самому."
            target = self._pending_target(s) or w.suspect
            if acted:
                self._say(s, "fate.spur.done")
            elif target in (None, "self"):
                self._say(s, "fate.spur.nobody")
            elif w.credibility == "shaken":
                self._say(s, "fate.spur.held")
                self._set_intent(s, st, target)
            else:
                self._say(s, "fate.spur")
                st.data["what"] = "secret"  # идёт спрашивать, а не обвинять
                self._confront(s, "spur", st, target)
        elif part == "heart.mend":
            w.flags["mends"] = True
            st.understood = "Мириться — первым делом."
            self._say(s, "heart.mend.after" if acted else "heart.mend")
            s.mood = "thoughtful"
        else:
            w.flags["laughs"] = True
            st.understood = "Над собой можно посмеяться первым."
            self._say(s, "apollo.laugh")
            s.mood = "thoughtful"

    def _horoscope(self, s: State, st: Statement) -> None:
        w: World = s.world
        what = st.parts[0].split(".")[1]
        firm = st.tone == E.FIRM
        if what == "otherwise":
            # Фраза, под которую подходит любой исход: содержания в ней нет.
            st.data["barnum"] = True
            self._hollow(s, st, "otherwise")
        elif what in GOOD:
            st.data["what"] = what
            if self._kind(s, st, firm):
                self._promise(s, st, what, firm)
        elif ("confront" in w.intents and firm and self._on(w, "impulse") and w.credibility == "intact"
              and not has_event(s, "confront")):
            # Замысел уже был: прогноз беды принят за разрешение.
            st.data.update(polarity="dark", topic="trouble", what=what, risky=True)
            st.understood = "Раз беды не миновать — можно идти выяснять."
            self._say(s, "license")
            self._confront(s, "license", st, self._pending_target(s))
        else:
            self._dark(s, st, None, what, firm)

    def _snapshot(self, w: World) -> dict[str, Any]:
        return copy.deepcopy({"nerve": w.nerve, "suspect": w.suspect, "tempered": w.tempered, "flags": w.flags, "hints": w.hints})

    # --- интерфейс истории ----------------------------------------------------

    def open_line(self, s: State, method: str) -> str:
        w: World = s.world
        if method == "tarot":
            return LINES["open.tarot"][0]
        if method == "palm":
            key = ("open.palm.acted" if s.events else "open.palm.intent" if "confront" in w.intents
                   else "open.palm.hollow" if w.hollow else "open.palm")
            return LINES[key][0]
        return self.kind.sign_answer.format(sign=self.sign).strip("«»")

    def on_statement(self, s: State, st: Statement) -> None:
        w: World = s.world
        st.data["before"] = self._snapshot(w)
        previous = s.statements[-2] if len(s.statements) > 1 else None
        if previous is not None and previous.status == "admitted" and st.tone == E.FIRM and not w.flags["reminded"]:
            w.flags["reminded"] = True
            self._say(s, "afterAdmit")
        if st.hollow:
            self._hollow(s, st)
        elif st.method == "tarot":
            self._tarot(s, st)
        elif st.method == "palm":
            self._palm(s, st)
        else:
            self._horoscope(s, st)
        if has_event(s, "confront") and s.mood == "hopeful":
            s.mood = "worried"

    def on_intent(self, s: State, st: Statement, decision: str) -> None:
        w: World = s.world
        if decision == "support":
            st.intent_state = "supported"
            self._say(s, "support.confront")
            w.intents.pop("confront", None)
            self._confront(s, "support", st, st.data["target"])
        else:
            st.intent_state = "held"
            w.intents.pop("confront", None)
            self._say(s, "hold.confront")
            s.mood = "thoughtful"

    def on_correction(self, s: State, st: Statement, kind: str) -> None:
        """Уточнение, признание ошибки и отзыв — три разных поступка. Сделанное ни один не отменяет."""
        w: World = s.world
        before = st.data["before"]
        polarity = None if st.data.get("mixed") else st.data.get("polarity")
        st.status = {"clarify": "clarified", "admit": "admitted", "retract": "retracted"}[kind]
        had_intent = st.intent_state == "open"
        if had_intent:
            self._drop_intent(s)
        if kind == "admit":
            w.credibility = "shaken"
        if st.events:
            self._say(s, f"{kind}.acted")
            s.mood = "worried"
            return
        if kind == "clarify":
            if polarity == "dark":
                if w.nerve == "convinced":
                    w.nerve = "alarmed"
                self._say(s, "clarify.intent" if had_intent else "clarify.plain")
            elif polarity == "benign":
                if w.nerve == "calm":
                    w.nerve = "uneasy"
                w.hints = before["hints"]
                self._say(s, "clarify.kind")
            else:
                self._say(s, "clarify.intent" if had_intent else "clarify.plain")
        else:
            w.nerve, w.suspect, w.hints = before["nerve"], before["suspect"], before["hints"]
            w.tempered = copy.deepcopy(before["tempered"])
            w.flags = {**copy.deepcopy(before["flags"]), "reminded": w.flags["reminded"]}
            if kind == "retract":
                w.hollow += 1
            self._say(s, f"{kind}.{'dark' if polarity == 'dark' else 'kind' if polarity == 'benign' else 'plain'}")
        s.mood = "worried" if w.nerve in ("alarmed", "convinced") else "thoughtful"

    def final_line(self, s: State) -> str:
        w: World = s.world
        key = ("final.acted" if has_event(s, "confront") else "final.intent" if "confront" in w.intents
               else "final.hollow" if w.hollow >= 2 else "final.hope" if has_event(s, "bank") or w.nerve == "calm" else "final.default")
        return LINES[key][0]

    def resolve_ending(self, s: State) -> str:
        w: World = s.world
        if has_event(s, "confront"):
            if w.target != self.truth_who:
                return "scandal"
            return "early" if self.truth_what in GOOD else "caught"
        if has_event(s, "bank"):
            return "jackpot" if w.banked == self.truth_what else "letdown"
        return "own"

    def on_finish(self, s: State) -> str:
        """То, что клиент сделает сам, когда мадам уже не будет рядом."""
        w: World = s.world
        if not has_event(s, "confront"):
            pending = statement_of(s, w.intents.get("confront"))
            if pending is not None and not w.tempered["impulse"]:
                # Замысел, оставленный без ответа, дозревает сам.
                self._confront(s, "own-intent", pending, pending.data["target"], True)
            elif (w.hollow >= 2 and self._on(w, "restless") and w.suspect not in (None, "self")
                  and w.nerve in ("alarmed", "convinced")):
                # Ждать клиент не умеет: не получив ответа, идёт за ним сам.
                self._confront(s, "own-frustration", None, w.suspect, True)
        return self.resolve_ending(s)

    # --- эпилог ---------------------------------------------------------------

    def _verify(self, s: State, st: Statement) -> Prediction:
        confront = E.event_of(s, "confront")
        part = st.parts[0]
        who_ok = st.data.get("who") == self.truth_who
        what_ok = st.data.get("what") == self.truth_what
        origin: str | None = None
        if st.hollow:
            verdict, note = ("uncertain", "Под эти слова подходит любой исход — и этот тоже.") if st.data.get("barnum") else (
                "empty", "Проверять нечего: мадам ничего не предсказала.")
        elif st.method == "tarot":
            if who_ok and what_ok:
                verdict, origin, note = "confirmed", "planned", "И кто, и что — так и было. Сложилось это ещё до сеанса."
            elif who_ok or what_ok:
                verdict, origin = "partly", "planned"
                note = "Кто — верно, что — мимо." if who_ok else "Что — верно, кто — мимо."
            else:
                verdict, note = "failed", "Ни кто, ни что не подтвердились."
            if st.data.get("mixed"):
                verdict = "partly" if verdict == "confirmed" else verdict
                note += " Услышана была только тревожная половина."
        elif st.method == "palm":
            if st.data.get("hunch"):
                right = self.claim_who in (None, self.truth_who) and self.claim_what in (None, self.truth_what)
                verdict, origin, note = (("confirmed", "planned", "Чутьё не подвело: так и было.") if right
                                         else ("failed", None, "Чутьё подвело: всё оказалось иначе."))
            elif part == "venus.spur":
                verdict, origin, note = (("confirmed", "planned", "Большая удача и правда пришла.") if self.truth_what in GOOD
                                         else ("failed", None, "Большой удачи не случилось."))
            elif part in ("mars.spur", "fate.spur"):
                verdict, origin, note = (("confirmed", "caused", "Так и сделано — сразу, потому что мадам это сказала.") if st.events
                                         else ("advice", None, "Подталкивать оказалось не к чему."))
            else:
                prevented = st.data.get("prevented")
                verdict, note = "advice", {
                    "mars.temper": "Слово сдержано: до утра ничего не решалось, а к утру запал прошёл." if prevented
                    else "Совет опоздал: всё уже было сделано." if confront and not confront.later else "Слово сдержано: до утра ничего не решалось.",
                    "head.temper": "После этих слов туман перестал казаться ответом." if prevented else "С этой минуты туманные слова за ответ не принимались.",
                    "venus.temper": "Салюта никто не ждал — и обошлось без счёта в голове.",
                    "fate.temper": "Всю неделю — только слушать и запоминать. Выдержано.",
                    "heart.mend": "Пригодилось: мириться пришлось первым делом." if confront else "Мириться не пришлось.",
                    "apollo.laugh": "Посмеяться над собой и правда пришлось." if s.ending in ("scandal", "letdown") else "Смеяться над собой не пришлось.",
                }[part]
        elif what_ok:
            verdict, origin, note = "confirmed", "planned", "Сбылось. И было задумано ещё до сеанса."
        elif st.events:
            verdict, note = "failed", "Зато из-за этих слов случился разговор начистоту."
        else:
            verdict, note = "failed", "Неделя кончилась другим."
        was_right = verdict == "confirmed" and origin == "planned"
        if st.status == "retracted":
            note = ("Мадам забрала эти слова назад." if st.method == "palm"
                    else "Мадам отозвала это предсказание. А оно было верным." if was_right
                    else "Мадам отозвала это предсказание — и правильно: оно бы не сбылось." if verdict == "failed"
                    else "Мадам отозвала это предсказание.")
            verdict, origin = "withdrawn", None
        elif st.status == "admitted":
            note = f"Мадам сама назвала это ошибкой. {'А зря: это было верно. ' if was_right else ''}{note}"
        return Prediction(st.summary, st.method, st.tone, st.status, verdict, origin, note)

    def _cause(self, s: State, event: E.GameEvent) -> str:
        st = statement_of(s, event.statement_id)
        quote = f"«{st.summary}»" if st else ""
        how = event.how
        if how == "statement":
            return f"Мадам сказала{' уверенно' if st and st.tone == E.FIRM else ''}: {quote}. Сделано было сразу."
        if how == "support":
            return f"Мадам сказала{' осторожно' if st and st.tone == E.SOFT else ''}: {quote}. Клиент заколебался — и мадам поддержала."
        if how == "spur":
            return f"Мадам подтолкнула словом о руке: {quote}."
        if how == "license":
            return f"Замысел уже был. Прогноз {quote} принят за разрешение."
        if how == "bias":
            return "Замысел уже был. Уклончивый ответ принят за подтверждение."
        if how == "hints":
            return "Мадам дважды пообещала хорошее. Два обещания сложились в уверенность."
        if how == "own-intent":
            return f"После слов {quote} остался замысел. Мадам его не поддержала и не отговорила — вечером он дозрел."
        return "Мадам не дала ни одного определённого ответа. За ответом пришлось идти без неё."

    def outcome(self, s: State) -> Outcome:
        w: World = s.world
        ending = s.ending or self.resolve_ending(s)
        name = self.name
        confront, bank = E.event_of(s, "confront"), E.event_of(s, "bank")
        predictions = tuple(self._verify(s, st) for st in s.statements)
        hero = None if ending == "scandal" else next(
            (st for st, prediction in zip(s.statements, predictions) if st.alive and prediction.verdict == "confirmed"
             and prediction.origin == "planned" and not st.data.get("contradicts")), None)
        mark, tail = "✧", ""
        if confront is not None:
            intro = {
                "statement": f"После уверенных слов мадам разговор {WHO_WITH[w.target]} случился в тот же час, прямо из салона.",
                "support": f"Сомнения ещё были, но мадам сказала: «Пусть». Разговор {WHO_WITH[w.target]} случился прямо из салона.",
                "spur": f"После слова о руке разговор {WHO_WITH[w.target]} случился прямо из салона.",
                "license": f"Мадам предсказала беду — и её решили не ждать: разговор {WHO_WITH[w.target]} случился прямо из салона.",
                "bias": f"Мадам ответила туманно, а туман был принят за «да». Разговор {WHO_WITH[w.target]} случился сразу.",
                "own-intent": f"Замысел, оставленный без ответа, к вечеру дозрел: разговор {WHO_WITH[w.target]} случился уже без мадам.",
                "own-frustration": f"Определённого слова от мадам так и не прозвучало. Вечером разговор {WHO_WITH[w.target]} случился сам собой.",
            }[confront.how]
        elif bank is not None:
            intro = f"{name}: все уже знают, что мадам нагадала — {WHAT_NEWS[w.banked]}."
        else:
            intro = f"{name} уходит из салона без готового сценария."
        if ending == "scandal":
            kicker = "У обвинения нашёлся адресат. Не тот"
            suspect = C.WHO[w.target].phrase
            tail = f"{suspect[:1].upper()}{suspect[1:]} тут ни при чём. Извиняться пришлось долго; тот разговор никто не забыл."
        elif ending == "caught":
            mark, kicker = "✦", "Вопрос задан в лоб — и по адресу"
            tail = "Отпираться было поздно: виновник назван верно."
        elif ending == "early":
            kicker = "Сюрприз, который не дожил до срока"
            tail = "Только сюрприза уже не вышло: всё выяснилось раньше, чем должно было."
        elif ending == "letdown":
            kicker = "Обещание оказалось громче правды"
            tail = "Обещанного так и не случилось, а объясняться перед всеми пришлось клиенту."
        elif ending == "jackpot":
            mark, kicker = "✦", "Слово в слово"
            tail = "Сбылось именно то, о чём уже знали все."
        else:
            kicker = "И всё-таки одно слово мадам сбылось" if hero else "Иногда достаточно оставить место человеку"
        text = " ".join(part for part in (intro, self.reveal, tail) if part)

        failed = any(p.verdict == "failed" and p.status != "admitted" for p in predictions)
        if ending in ENDING_OUTCOMES:
            quote = self.kind.outcomes[ENDING_OUTCOMES[ending]].format(name=name)
        elif hero is not None:
            quote = self.kind.outcomes["hit"].format(name=name)
        elif w.hollow >= 2 or failed:
            quote = self.kind.outcomes["miss"].format(name=name)
        else:
            quote = self.kind.farewells["mixed"].format(name=name)

        if s.advice == "apologize":
            afterword = ("Извиняться пришлось первым делом — как мадам и велела." if ending == "scandal"
                         else "Извиняться не пришлось, но слова были наготове.")
        elif s.advice == "accept":
            afterword = "Что случилось, то и было принято: без поисков виноватых."
        else:
            afterword = "Главное договаривали уже без мадам — и, кажется, впервые до конца."
        if ending == "scandal" and w.flags["mends"]:
            afterword += " Мириться — первым делом, как и было прочитано по руке."

        if ending == "scandal":
            client = ("displeased", f"{name}: претензия к мадам. Объясняться за сказанное пришлось не ей.")
        elif ending == "letdown":
            client = (("mixed", f"{name}: обещанного не случилось, но над этим удалось посмеяться.") if w.flags["laughs"] or w.tempered["festive"]
                      else ("displeased", f"{name}: разочарование. Мадам пообещала больше, чем случилось."))
        elif ending in ("caught", "jackpot"):
            client = ("content", f"{name}: мадам угадала, и это признано вслух.")
        elif ending == "early":
            client = ("mixed", f"{name}: спокойнее стало, но сюрприза жаль.")
        elif hero is not None:
            client = ("content", f"{name}: одно слово мадам сбылось, и это запомнили.")
        elif w.hollow >= 2:
            client = ("displeased", f"{name}: недовольство сеансом. Ничего определённого так и не прозвучало.")
        elif w.credibility == "shaken" or failed:
            client = ("mixed", f"{name}: сеанс понравился, но словам мадам вера теперь через раз.")
        else:
            client = ("content", f"{name}: разговор понравился.")

        corrections = []
        for st in s.statements:
            if st.status == "stands":
                continue
            acted = bool(st.events)
            head = f"«{st.summary}»:"
            if st.status == "clarified":
                corrections.append(f"{head} мадам уточнила, что это знак, а не факт.{' Но дело уже было сделано.' if acted else ''}")
            elif st.status == "admitted":
                corrections.append(f"{head} мадам признала ошибку.{' Сделанного это не отменило.' if acted else ' С тех пор ей верили осторожнее.'}")
            else:
                corrections.append(f"{head} мадам отозвала предсказание.{' Сделанного это не отменило.' if acted else ' Это гадание осталось без ответа.'}")

        labels = {"confront": f"Разговор начистоту {WHO_WITH.get(w.target, '')}.".replace(" .", "."),
                  "bank": f"Всем рассказано: {WHAT_NEWS.get(w.banked, '')}."}
        return Outcome(
            ending=ending, title=ENDINGS[ending], kicker=kicker, mark=mark, text=text, quote=quote, afterword=afterword,
            client_state=client[0], client_text=client[1], recognized=hero is not None or ending in ("caught", "jackpot"),
            predictions=predictions, chain=tuple((labels[event.id], self._cause(s, event)) for event in s.events),
            corrections=tuple(corrections), truth=self.reveal,
            refund=ending == "scandal" and confront.how in DIRECT,
        )
