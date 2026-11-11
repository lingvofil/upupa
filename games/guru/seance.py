"""Сеанс: первый срез новой концепции салона. Один клиент, зато приём ведёт сама мадам.

В обычной смене гадания помогают восстановить заранее заданные «кто / что / когда». Здесь
иначе: скрытую правду никто не угадывает. Мадам слушает Людмилу, сама связывает образы с её
словами и решает, что и как произнести, а от сказанного зависит, как Людмила поступит и чем
кончится её суббота.

Три гадания отличаются действием, а не только картинкой:

* Таро — связать две карты из трёх с рассказом; у каждой пары два авторских прочтения;
* ладонь — осмотреть две области из трёх, прочитать приметы и выбрать, как их истолковать;
* гороскоп — выбрать, как идти к известной дате: прояснить сегодня, договориться или ждать.

Каждое прочтение можно произнести как версию, как утверждение или оставить при себе. Версия
бережёт отношения, но весит меньше; утверждение весит больше в обе стороны. Слово «возможно»
от последствий не освобождает: содержание Людмила слышит в любом случае. Реакции учитывают,
что говорилось раньше. Ни один метод не обязателен: на прощание мадам даёт напутствие.

Без Telegram: ядро отдаёт события (``DisplayEvent``) и ``SeanceView``, как смена и живой приём.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from games.guru import rules, salon
from games.guru.contract import Action, DisplayEvent, SeanceResult, SeanceView
from games.guru.model import Participant

CLIENT = "Людмила"
ARCHETYPE = "jealous"  # кто нарисован за столом
BASE_FEE = 2000
IDLE_FEE = 300  # если мадам так ничего и не сказала
QUESTIONS = 2
TRUST_START = 45
CLAIM = "claim"
IDEA = "idea"
DRAFT = "draft"
CARDS = ("porcelain", "asteroids", "lighthouse")  # рисунки расклада: позиции в этом приёме постоянные

ARRIVAL = "🚪 Заходит Людмила. Садится на край стула и кладёт на стол распечатку звонков, размеченную маркером."
TOLD = (
    "— По вторникам и четвергам он пропадает. Говорит, «курсы повышения». Какие курсы, Гена сантехник двадцать лет, "
    "ему самому впору преподавать.",
    "— Приходит и напевает. Напевает! Захожу на кухню — замолкает и смотрит в кастрюлю.",
    "— В телефоне какая-то Алла Борисовна: «Сегодня были молодцом». Я спросила прямо: у тебя женщина? "
    "А он: «В субботу услышишь и всё поймёшь». В субботу у нас годовщина, двадцать пять лет.",
    "— Я неделю места себе не нахожу. Скажите, что происходит. Или хотя бы что мне делать до субботы.",
)


@dataclass(frozen=True)
class Question:
    id: str
    label: str
    answer: str


QUESTIONS_LIST = (
    Question("seen", "Вы её видели?",
             "— Аллу Борисовну? Фото в профиле: женщина лет семидесяти с начёсом и аккордеоном. Хитро придумал, да? "
             "Кто ж на такую подумает."),
    Question("sing", "Что он напевает?",
             "— «Милая, ты услышь меня». Фальшиво, но старательно. Раньше он пел, только когда прищемит палец."),
    Question("party", "Как вы отмечаете?",
             "— Никак. Я не люблю, когда на меня все смотрят. Его мать хочет на серебряную свадьбу ресторан и тамаду. "
             "Я сказала: только через мой труп. Гена промолчал."),
    Question("want", "Чего хотите вы?",
             "— Чтобы меня спросили. Хоть раз. Не «сюрприз», не «потерпи», а «Люда, ты как хочешь?»"),
)
QUESTION_BY_ID: Mapping[str, Question] = MappingProxyType({question.id: question for question in QUESTIONS_LIST})


@dataclass(frozen=True)
class Reading:
    """Авторское прочтение: что мадам может произнести и что из этого выйдет."""

    code: str
    method: str  # tarot | palm | horo
    short: str  # подпись на кнопке
    link: str  # какие образы или приметы с чем связаны
    voice: str  # что произносит мадам
    reply: str  # ответ Людмилы на версию
    reply_claim: str  # ответ на утверждение
    trust: int  # как меняется доверие
    aside: str = ""  # что при этом делает Людмила
    plan: Mapping[str, float] = field(default_factory=dict)  # press | terms | wait — к чему подталкивает
    belief: Mapping[str, float] = field(default_factory=dict)  # cheat | learn | signal | hobby — во что она поверит
    seen: bool = False  # Людмила чувствует, что её заметили


READINGS: Mapping[str, Reading] = MappingProxyType({reading.code: reading for reading in (
    Reading("t1", "tarot", "Стесняется показать",
            "Живой мир внутри головы и табличка «Я не готов» — его напевание и курсы.",
            "Он осваивает что-то, что стесняется показать",
            "— Стесняется? Гена? Он в бане при всех поёт… Хотя при мне петь перестаёт.",
            "— Двадцать пять лет не стеснялся, а тут начал. Хотя при мне он петь и правда перестаёт.",
            4, "В голосе у неё впервые не обида, а любопытство.", belief={"learn": 1}),
    Reading("t2", "tarot", "Другая близость",
            "Трещины и разделённые миры — его исчезновения и сообщения.",
            "Он скрывает другую близость и не готов признаться",
            "— Вот. Значит, я не выдумала.",
            "— Вот! Я знала. Карты врать не будут. Врать будет он.",
            8, "Людмила придвигает к себе распечатку.", plan={"press": 1}, belief={"cheat": 1}),
    Reading("t3", "tarot", "Донести чувство",
            "Скрытый мир и свет под водой — его «услышишь» и ваша годовщина.",
            "Он пытается донести чувство необычным способом",
            "— Донести чувство? И поэтому мне неделю места себе не находить?",
            "— Чувство. Необычным способом. А обычным, словами через рот, он не пробовал? …Ладно. Допустим.",
            3, "Она сердится, но распечатку переворачивает лицом вниз.", plan={"wait": 1}, belief={"signal": 1}),
    Reading("t4", "tarot", "Вас не замечают",
            "Трещины и свет, который трудно увидеть, — её тревога.",
            "Главная проблема сейчас — вы не чувствуете, что вас замечают",
            "— Да. Даже если там ничего нет, я здесь одна со своими догадками.",
            "— Откуда вы… Да. Даже если там ничего нет, я здесь одна со своими догадками.",
            8, seen=True),
    Reading("t5", "tarot", "Отдельное увлечение",
            "Отдельные миры и далёкий маяк — занятия, о которых он не рассказывает.",
            "Это его отдельное увлечение, не связанное с вашим праздником",
            "— То есть у него новая жизнь, а я просто должна привыкнуть?",
            "— Отдельное. Двадцать пять лет всё было общее, включая зубную пасту, а теперь отдельное.",
            -4, plan={"terms": 1}, belief={"hobby": 1}),
    Reading("t6", "tarot", "Оба ждут сигнала",
            "Нити между мирами и сигнал маяка — разговор, который не получился.",
            "Связь есть, но каждый ждёт, что другой сам поймёт сигнал",
            "— Я спросила про женщину. Он ответил про субботу. Разговариваем прекрасно.",
            "— Я спросила про женщину. Он ответил про субботу. Прекрасная у нас связь. …Но есть, говорите.",
            5, plan={"terms": 1}),
    Reading("l1", "palm", "Право решить самой",
            "Сердце раздваивается, а голова идёт рядом, но своей дорогой.",
            "Вам нужна близость, в которой остаётся право решить самой",
            "— Мне мешает, что за меня решили, как радоваться.",
            "— Да. Мне мешает, что за меня решили, как радоваться.",
            6, plan={"terms": 1}, seen=True),
    Reading("l2", "palm", "Ясность дороже сюрприза",
            "Сердце раздваивается, а голова идёт рядом, но своей дорогой.",
            "Сейчас ясность для вас дороже сохранённого сюрприза",
            "— Даже если я всё испорчу?.. Пожалуй, узнать я действительно хочу.",
            "— Даже если я всё испорчу? …Да. Узнать я хочу больше.",
            4, plan={"press": 1}),
    Reading("l3", "palm", "Ждать на своих условиях",
            "Сердце раздваивается, а большой палец держится на расстоянии.",
            "Вы готовы дать ему шанс, если условия ожидания выберете сами",
            "— Не просто молчать, а договориться. Это другое.",
            "— Не просто молчать, а договориться. Это я могу.",
            7, "Людмила разжимает руку.", plan={"terms": 2}),
    Reading("l4", "palm", "Выдержать праздник",
            "Сердце раздваивается, а большой палец держится на расстоянии.",
            "Ради его жеста вы способны выдержать неудобный праздник",
            "— Опять мне надо потерпеть, чтобы всем было хорошо?",
            "— Способна. Я на многое способна. Опять мне надо потерпеть, чтобы всем было хорошо?",
            -8, "Людмила убирает руку со стола.", plan={"wait": 1}),
    Reading("l5", "palm", "Спросить без приговора",
            "Голова ровная, а большой палец отставлен и напряжён.",
            "Вы можете спросить прямо, не превращая вопрос в приговор",
            "— Без «я всё знаю»? Это будет новый жанр.",
            "— Без «я всё знаю»? Новый жанр. Ну, попробую без распечатки.",
            5, plan={"press": 1}, belief={"calm": 1}),
    Reading("l6", "palm", "Надавить",
            "Голова ровная, а большой палец отставлен и напряжён.",
            "Без давления он так и будет уходить от ответа",
            "— Значит, сегодня прижму.",
            "— Значит, сегодня прижму. К стенке. Он у меня и не такое рассказывал.",
            2, "Людмила сжимает ладонь в кулак.", plan={"press": 2}, belief={"harsh": 1}),
    Reading("g1", "horo", "Прояснить сегодня",
            "Разговор — в сегодняшнем окне. Меньше неизвестности, меньше сюрприза.",
            "Если открыть разговор сейчас, тайна может раскрыться раньше праздника. Возможность удивить вас уменьшится",
            "— Раньше праздника. Зато хоть высплюсь.",
            "— Раньше праздника, значит. Зато хоть высплюсь.",
            3, plan={"press": 2}),
    Reading("g2", "horo", "Условия, потом ждать",
            "Сегодня — сказать, чего хочешь; суббота — дать событию случиться. Несколько дней придётся терпеть неопределённость.",
            "Можно изменить форму предстоящего события, оставив его содержание неизвестным",
            "— Сказать, чего хочу, и не спрашивать, что он задумал? …Я попробую.",
            "— Сказать, чего хочу, и не спрашивать, что он задумал. Сложно. Но попробую.",
            3, plan={"terms": 2}),
    Reading("g3", "horo", "Не трогать до субботы",
            "Всё действие — в субботу. Неожиданность сохранится, повлиять на подготовку уже не выйдет.",
            "Если сейчас ничего не менять, его замысел проявится целиком. Форма может оказаться не вашей",
            "— До субботы три дня. Я за три дня обои сгрызу. Но ладно.",
            "— Целиком. Не в моей форме. До субботы три дня, я обои сгрызу. Но ладно.",
            3, plan={"wait": 2}),
)})

# Таро: пара карт → два прочтения. h — голова, a — астероиды, l — маяк.
TAROT_PAIRS: Mapping[str, tuple[str, tuple[str, str]]] = MappingProxyType({
    "ha": ("Голова + Астероиды", ("t1", "t2")),
    "hl": ("Голова + Маяк", ("t3", "t4")),
    "al": ("Астероиды + Маяк", ("t5", "t6")),
})
TAROT_TEXT = "\n\n".join((
    "🃏 Три карты ложатся на стол. У каждого места свой вопрос.",
    "Что внутри — Фарфоровая голова\nСнаружи трещины, внутри живой мир: джунгли, и по осколкам уже пасутся коровы.",
    "Что между — Встреча астероидов\nДва отдельных мира связаны нитями, а человек на краю держит табличку «Я не готов».",
    "Как дойдёт — Маяк на дне океана\nСвет под водой: маяк светит издалека, и не всякий разглядит такой сигнал.",
))
TAROT_RITUAL = "Мадам сдвигает колоду левой рукой и долго молчит…"

AREAS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "heart": ("Сердце", "Сердце: линия выраженная, в конце расходится на две ветви."),
    "head": ("Голова", "Голова: линия ровная, идёт близко к линии сердца, но отдельно."),
    "thumb": ("Большой палец", "Большой палец: между ним и ладонью заметный промежуток. Когда Людмила напрягается, рука сжимается."),
})
PALM_PAIRS: Mapping[frozenset, tuple[str, str]] = MappingProxyType({
    frozenset(("heart", "head")): ("l1", "l2"),
    frozenset(("heart", "thumb")): ("l3", "l4"),
    frozenset(("head", "thumb")): ("l5", "l6"),
})
PALM_LIMIT = 2
PALM_RITUAL = "Мадам надевает очки и берёт Людмилу за запястье…"
PALM_TEXT = "🖐 Людмила кладёт руку на стол ладонью вверх. Долго держать её она не даст: осмотреть получится две области из трёх."

HORO_CODES = ("g1", "g2", "g3")
HORO_RITUAL = "Мадам рисует на салфетке три окна и обводит субботу…"
HORO_TEXT = ("♈ На салфетке три окна: сегодня, дни до субботы и сама суббота. Годовщина никуда не денется: "
             "вопрос в том, как к ней идти.")
HORO_CHANGE_VOICE = "Я предлагаю другой вариант, потому что звёзды сдвинулись"
HORO_CHANGE_REPLY = "— Минуту назад звёзды стояли иначе."
HORO_CHANGE_TRUST = -5

METHODS: Mapping[str, str] = MappingProxyType({"tarot": "🃏 Таро", "horo": "♈ Гороскоп", "palm": "🖐 Ладонь"})
METHOD_ICONS: Mapping[str, str] = MappingProxyType({"tarot": "🃏", "horo": "♈", "palm": "🖐"})
VOICE_LEADS: Mapping[tuple[str, str], str] = MappingProxyType({
    ("tarot", IDEA): "Я бы рассмотрела такую версию",
    ("tarot", CLAIM): "Карты говорят прямо",
    ("palm", IDEA): "Мне кажется, рука говорит вот о чём",
    ("palm", CLAIM): "Рука говорит прямо",
    ("horo", IDEA): "Звёзды допускают такой путь",
    ("horo", CLAIM): "Звёзды говорят прямо",
})
MODE_LABELS: Mapping[str, str] = MappingProxyType({IDEA: "💬 Как версию", CLAIM: "❗ Как утверждение", DRAFT: "📝 Оставить при себе"})
MODE_HINT = ("Версия — Людмила поймёт, что ей предлагают подумать. Утверждение — примет за твоё знание. "
             "Содержание она услышит в любом случае.")
DRAFT_LINE = "Мадам делает пометку в тетради и вслух ничего не говорит."

FAREWELLS: Mapping[str, tuple[str, str, str, str]] = MappingProxyType({
    # кнопка, что говорит мадам, ответ Людмилы, к чему подталкивает
    "press": ("Поговорить сегодня", "Поговорите с ним сегодня", "— Сегодня так сегодня.", "press"),
    "terms": ("Назвать свои условия", "Скажите ему, чего хотите вы, и дождитесь субботы",
              "— Чего хочу я. Надо же, придётся сформулировать.", "terms"),
    "wait": ("Ждать субботы", "Не трогайте его до субботы", "— Три дня. Засекаю.", "wait"),
    "own": ("Решать ей самой", "Дальше решать вам", "— Мне, значит. Как обычно.", ""),
})
FAREWELL_WEIGHT = 2.0
BYE_LINE = "Мадам откладывает реквизит. Людмила берётся за сумку и не уходит: «Так что мне делать?»"

STATUS_BANDS = (
    (70, "🙂 Людмила придвигает стул ближе и больше не смотрит на часы."),
    (45, "Людмила слушает и крутит обручальное кольцо."),
    (30, "🙁 Людмила поджимает губы и поглядывает на дверь."),
    (0, "🙁 Людмила застёгивает сумку. Пока только сумку."),
)
ASKED_STATUS = "Людмила отвечает и разглаживает распечатку ладонью."


# --- реплики, которые зависят от уже сказанного ------------------------------

@dataclass(frozen=True)
class Echo:
    """Добавка к реакции: срабатывает, если до этого прозвучало или было спрошено нужное."""

    codes: tuple[str, ...]  # на какие прочтения
    after: tuple[str, ...]  # что должно было прозвучать раньше (код) или быть спрошено («q:…»)
    line: str
    trust: int = 0


ECHOES = (
    Echo(("l6",), ("t2",), "— И про ту, другую, я его тоже спрошу. С порога.", 0),
    Echo(("t2",), ("l6",), "— Теперь я хотя бы знаю, о чём его прижимать.", 0),
    Echo(("l3", "g2"), ("t1", "t3"), "— Если он и правда что-то готовит, пусть готовит. Но не через мою голову.", 3),
    Echo(("l1",), ("t4",), "— Вы второй раз говорите то, чего он не сказал ни разу.", 4),
    Echo(("t4",), ("l1",), "— Вы второй раз говорите то, чего он не сказал ни разу.", 4),
    Echo(("t3",), ("q:sing",), "— «Услышь меня»… Он же это и поёт.", 3),
    Echo(("t2",), ("q:seen",), "— Хотя ей семьдесят и она с аккордеоном. …Нет, карты же зря не скажут?", 0),
    Echo(("l4",), ("q:party",), "— Я вам только что сказала про ресторан и тамаду.", -4),
    Echo(("l1", "l3"), ("q:want",), "— Я же говорила: чтобы меня спросили.", 3),
)

# Прочтения, которые спорят друг с другом: вторым звучит то, что противоречит первому.
CONFLICTS = (
    (frozenset(("t1", "t3")), frozenset(("t2",))),
    (frozenset(("t3",)), frozenset(("t5",))),
    (frozenset(("l5",)), frozenset(("l6",))),
    (frozenset(("l3",)), frozenset(("l4",))),
)
CONFLICT_SOFT = "— То одна версия, то другая. Ладно, версии так версии."
CONFLICT_HARD = "— Погодите. Вы только что говорили другое: «{previous}». Так что из этого правда?"
CONFLICT_TRUST = -7


@dataclass(frozen=True)
class Voiced:
    code: str
    mode: str  # idea | claim


@dataclass
class Seance:
    """Партия-сеанс: одна мадам, одна Людмила."""

    session_id: str
    chat_id: int
    host: Participant
    started_at: float
    last_activity: float
    idle_limit: float
    wallet_start: int
    items: tuple[str, ...] = ()
    gifts: tuple[str, ...] = ()
    phase: str = "hub"  # hub | ask | tarot | palm | pick | mode | farewell | done
    method: str | None = None  # какое гадание сейчас идёт
    options: tuple[str, ...] = ()  # прочтения, из которых мадам выбирает
    pending: str | None = None  # выбранное прочтение ждёт решения, как его сказать
    areas: list[str] = field(default_factory=list)  # осмотренные области ладони
    questions_left: int = QUESTIONS
    asked: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)  # гадания, которые уже проведены
    voiced: list[Voiced] = field(default_factory=list)
    drafts: list[str] = field(default_factory=list)
    heard: list[str] = field(default_factory=list)  # что сказала Людмила: мадам может перечитать
    echoed: list[str] = field(default_factory=list)  # добавки к реакциям, которые уже прозвучали
    trust: int = TRUST_START
    horo_changed: bool = False
    farewell: str | None = None
    ending: str | None = None
    fee: int = 0
    step: int = 0
    finished: bool = False
    finish_reason: str | None = None
    message_id: int | None = None
    thread_id: int | None = None

    def is_participant(self, user_id: int) -> bool:
        return self.host.user_id == user_id

    def said(self, code: str) -> Voiced | None:
        return next((voiced for voiced in self.voiced if voiced.code == code), None)


def new_session(*, session_id: str, chat_id: int, host_id: int, host_name: str, now: float, wallet: int = 0,
                items: tuple[str, ...] = (), gifts: tuple[str, ...] = ()) -> Seance:
    session = Seance(
        session_id=session_id, chat_id=chat_id, host=Participant(host_id, host_name), started_at=now, last_activity=now,
        idle_limit=rules.IDLE_TIMEOUT_S, wallet_start=wallet, items=tuple(items),
        gifts=tuple(gift.key for gift in salon.owned_gifts(gifts)),
    )
    session.heard.extend(TOLD)
    return session


def _event(kind: str, text: str, pace: tuple[float, float] = rules.NO_PAUSE, **data: Any) -> DisplayEvent:
    return DisplayEvent(kind, text, pause=pace[0], spread=pace[1], data=data)


def arrival_events(session: Seance) -> tuple[DisplayEvent, ...]:
    """Людмила заходит и рассказывает. Рассказ длиннее, чем в смене: торопиться некуда."""
    scene = (ARCHETYPE, session.items, session.gifts)
    return (_event("line", ARRIVAL, scene=scene), *(_event("tell", part, rules.PACE_TELL) for part in TOLD))


# --- что можно нажать -------------------------------------------------------

def _horo_open(session: Seance) -> bool:
    """Гороскоп можно один раз пересмотреть, но только если прогноз уже прозвучал."""
    if "horo" not in session.closed:
        return True
    return not session.horo_changed and any(READINGS[voiced.code].method == "horo" for voiced in session.voiced)


def available_actions(session: Seance) -> tuple[Action, ...]:
    if session.finished:
        return ()
    phase = session.phase
    if phase == "hub":
        actions = []
        if session.questions_left > 0:
            actions.append(Action("ask", f"❓ Уточнить ({session.questions_left})", "hub"))
        for key, label in METHODS.items():
            if key == "horo" and "horo" in session.closed and _horo_open(session):
                actions.append(Action("horo", "♈ Изменить прогноз", "hub"))
            elif key not in session.closed:
                actions.append(Action(key, label, "hub"))
        return (*actions, Action("heard", "📜 Что она сказала", "control"), Action("bye", "🚪 Проводить", "control"))
    if phase == "ask":
        left = [Action(f"q:{question.id}", question.label, "question") for question in QUESTIONS_LIST if question.id not in session.asked]
        return (*left, Action("back", "↩ Назад", "control"))
    if phase == "tarot":
        return tuple(Action(f"p:{key}", label, "choice") for key, (label, _) in TAROT_PAIRS.items())
    if phase == "palm":
        return tuple(Action(f"a:{key}", title, "area") for key, (title, _) in AREAS.items() if key not in session.areas)
    if phase == "pick":
        marks = ("А", "Б", "В")
        picks = [Action(f"r:{code}", f"{marks[index]}. {READINGS[code].short}", "choice") for index, code in enumerate(session.options)]
        if session.method == "tarot":
            picks.append(Action("back", "↩ Другие карты", "control"))
        return tuple(picks)
    if phase == "mode":
        return (*(Action(f"v:{mode}", label, "choice") for mode, label in MODE_LABELS.items()), Action("back", "↩ Назад", "control"))
    if phase == "farewell":
        return tuple(Action(f"f:{key}", label, "choice") for key, (label, *_) in FAREWELLS.items())
    return ()


# --- ход --------------------------------------------------------------------

def _status(session: Seance) -> str:
    return next(line for threshold, line in STATUS_BANDS if session.trust >= threshold)


def _set_trust(session: Seance, delta: int) -> None:
    session.trust = max(0, min(100, session.trust + delta))


def _hear(session: Seance, events: list[DisplayEvent], line: str, pace: tuple[float, float] = rules.NO_PAUSE) -> None:
    """Слова Людмилы: показать и запомнить, чтобы мадам могла перечитать."""
    session.heard.append(line)
    events.append(_event("answer", line, pace))


def _ask(session: Seance, question_id: str, events: list[DisplayEvent], metrics: list) -> None:
    question = QUESTION_BY_ID[question_id]
    session.asked.append(question.id)
    session.questions_left -= 1
    session.phase = "hub"
    events.append(_event("ask", f"— Ты: {rules.quote(question.label)}", rules.PACE_ANSWER))
    _hear(session, events, question.answer)
    events.append(_event("status", ASKED_STATUS))
    metrics.append(("action", {"kind": "question", "mode": "seance", "question": question.id, "left": session.questions_left}))


def _open_method(session: Seance, method: str, events: list[DisplayEvent]) -> None:
    session.method = method
    if method == "tarot":
        session.phase = "tarot"
        events.append(_event("ritual", TAROT_RITUAL))
        events.append(_event("omen", "", rules.PACE_RITUAL, cards=list(CARDS)))  # сам расклад описан на экране шага
    elif method == "palm":
        session.phase = "palm"
        session.areas.clear()
        events.append(_event("ritual", PALM_RITUAL))
        events.append(_event("omen", "", rules.PACE_RITUAL, drawing=("study", ())))
    else:
        session.phase = "pick"
        session.options = tuple(code for code in HORO_CODES if session.said(code) is None)
        events.append(_event("ritual", HORO_RITUAL))
        events.append(_event("omen", "", rules.PACE_RITUAL))


def _inspect(session: Seance, area: str, events: list[DisplayEvent]) -> None:
    session.areas.append(area)
    if len(session.areas) < PALM_LIMIT:
        return
    session.options = PALM_PAIRS[frozenset(session.areas)]
    session.phase = "pick"
    # Рисунок с подсветкой показываем один раз, когда выбор областей сделан.
    events.append(_event("omen", "🖐 Мадам водит пальцем по ладони.", rules.PACE_REACTION, drawing=("study", tuple(session.areas))))


def _conflict(session: Seance, code: str, mode: str) -> tuple[str, int] | None:
    """Спорит ли новое прочтение с тем, что уже прозвучало."""
    for first, second in CONFLICTS:
        for mine, theirs in ((first, second), (second, first)):
            if code not in mine:
                continue
            previous = next((voiced for voiced in reversed(session.voiced) if voiced.code in theirs), None)
            if previous is None:
                continue
            if CLAIM in (mode, previous.mode):
                return CONFLICT_HARD.format(previous=READINGS[previous.code].voice), CONFLICT_TRUST
            return CONFLICT_SOFT, 0
    return None


def _heard_before(session: Seance, mark: str) -> bool:
    return mark[2:] in session.asked if mark.startswith("q:") else session.said(mark) is not None


def _voice(session: Seance, mode: str, events: list[DisplayEvent], metrics: list) -> None:
    reading = READINGS[session.pending]
    change = reading.method == "horo" and "horo" in session.closed
    session.pending, session.options, session.phase = None, (), "hub"
    if reading.method not in session.closed:
        session.closed.append(reading.method)
    if mode == DRAFT:
        session.drafts.append(reading.code)
        events.append(_event("line", DRAFT_LINE))
        metrics.append(("action", {"kind": "draft", "mode": "seance", "code": reading.code}))
        return

    lead = HORO_CHANGE_VOICE if change else VOICE_LEADS[reading.method, mode]
    body = reading.voice[:1].lower() + reading.voice[1:]
    events.append(_event("voice", f"— Ты: {rules.quote(f'{lead}: {body}')}", rules.PACE_REACTION))
    before = session.trust
    conflict = _conflict(session, reading.code, mode)
    if change:
        session.horo_changed = True
        # Старый прогноз снят: в силе остаётся только новый.
        session.voiced = [voiced for voiced in session.voiced if READINGS[voiced.code].method != "horo"]
        _hear(session, events, HORO_CHANGE_REPLY, rules.PACE_ANSWER)
        _set_trust(session, HORO_CHANGE_TRUST)
    if conflict is not None:
        _hear(session, events, conflict[0], rules.PACE_ANSWER)
        _set_trust(session, conflict[1])
    _hear(session, events, reading.reply_claim if mode == CLAIM else reading.reply, rules.PACE_ANSWER)
    _set_trust(session, reading.trust)
    echo = next((item for item in ECHOES if reading.code in item.codes and item.line not in session.echoed
                 and any(_heard_before(session, mark) for mark in item.after)), None)
    if echo is not None:
        session.echoed.append(echo.line)
        _hear(session, events, echo.line)
        _set_trust(session, echo.trust)
    if reading.aside:
        events.append(_event("react", reading.aside))
    session.voiced.append(Voiced(reading.code, mode))
    events.append(_event("status", _status(session)))
    metrics.append(("action", {
        "kind": "voice", "mode": "seance", "code": reading.code, "method": reading.method, "as": mode, "change": change,
        "conflict": conflict is not None, "echo": echo is not None, "trust": session.trust, "trust_delta": session.trust - before,
        "order": len(session.voiced),
    }))


# --- развязка ---------------------------------------------------------------

@dataclass(frozen=True)
class Ending:
    key: str
    title: str
    text: str
    tip: int  # что случилось с деньгами мадам потом


def _weights(session: Seance, field_name: str) -> dict[str, float]:
    """Сложить, к чему подталкивали слова мадам. Утверждение весит вдвое больше версии."""
    totals: dict[str, float] = {}
    for voiced in session.voiced:
        for key, value in getattr(READINGS[voiced.code], field_name).items():
            totals[key] = totals.get(key, 0.0) + value * (2 if voiced.mode == CLAIM else 1)
    return totals


def plan_of(session: Seance) -> str | None:
    """Что Людмила сделает до субботы. ``None`` — ей так ничего и не сказали."""
    plan = _weights(session, "plan")
    push = FAREWELLS[session.farewell][3] if session.farewell else ""
    if push:
        plan[push] = plan.get(push, 0.0) + FAREWELL_WEIGHT
    if not plan:
        return None
    best = max(plan.values())
    leaders = [key for key, value in plan.items() if value == best]
    if len(leaders) == 1 or push in leaders:
        return push if push in leaders else leaders[0]
    # Поровну: перевешивает то, что прозвучало последним.
    for voiced in reversed(session.voiced):
        latest = [key for key in READINGS[voiced.code].plan if key in leaders]
        if latest:
            return latest[0]
    return leaders[0]


def _facts_bonus(session: Seance) -> tuple[int, str]:
    """Что мадам сказала о самом Гене: верное знание запоминают, неверное тоже."""
    bonus, notes = 0, []
    right = [session.said(code) for code in ("t1", "t3") if session.said(code)]
    wrong_claim = any(voiced.mode == CLAIM for voiced in (session.said("t2"), session.said("t5")) if voiced)
    if right and not wrong_claim:
        if any(voiced.mode == CLAIM for voiced in right):
            bonus += 1000
            notes.append("Людмила пишет подругам: «Девочки, она видит!!!»")
        else:
            bonus += 300
            words = " и ".join(phrase for code, phrase in (("t1", "«стесняется показать»"), ("t3", "«донести чувство»")) if session.said(code))
            notes.append(f"Про {words} Людмила потом вспоминала не раз.")
    hobby = session.said("t5")
    if hobby is not None and hobby.mode == CLAIM:
        bonus -= 500
        notes.append("«Отдельное увлечение, говорили? Он полгода пел про меня». Часть гонорара пришлось вернуть.")
    return bonus, " ".join(notes)


def resolve(session: Seance) -> Ending:
    """Чем кончилась суббота. Зависит от того, что и как сказала мадам, а не от угаданного ответа."""
    plan = plan_of(session)
    belief = _weights(session, "belief")
    cheat, harsh = belief.get("cheat", 0.0), belief.get("harsh", 0.0) > 0
    softened = belief.get("learn", 0.0) + belief.get("signal", 0.0) + belief.get("calm", 0.0)
    seen = any(READINGS[voiced.code].seen for voiced in session.voiced) or belief.get("signal", 0.0) > 0
    bonus, note = _facts_bonus(session)
    tail = f" {note}" if note else ""

    if plan is None:
        return Ending("nothing", "Ушла ни с чем",
                      "Людмила ушла с тем же, с чем пришла, минус гонорар. В субботу посреди ресторана, между горячим и тамадой, "
                      "Гена встал и запел «Милая, ты услышь меня»: полгода ходил к педагогу по вокалу. Сорок гостей снимали на телефон. "
                      "В отзыве о салоне она написала: «Смотрела красиво. Не сказала ничего».", 0)
    if plan == "press" and (cheat >= 2 or (cheat > 0 and (harsh or softened == 0))):
        refund = session.said("t2") is not None and session.said("t2").mode == CLAIM
        text = ("В тот же вечер Людмила встретила Гену в дверях с распечаткой: «Я всё знаю про твою Аллу Борисовну». "
                "Гена молча достал из сумки папку с нотами. «Милая, ты услышь меня». Полгода, по вторникам и четвергам, "
                "у педагога по вокалу семидесяти лет. Хотел спеть в субботу. Годовщину отметили молча.")
        if refund:
            text += (" Через день Людмила вернулась в салон: «Вы сказали — другая близость. Я чуть мужа не выгнала из-за "
                     "учительницы пения». Гонорар пришлось вернуть с извинениями.")
            return Ending("scandal", "Скандал с распечаткой", text, -1500)
        return Ending("scandal", "Скандал с распечаткой", text + " В салон она больше не приходила: версию про другую близость "
                      "она запомнила лучше всех остальных.", min(bonus, 0))
    if plan == "press":
        return Ending("talk", "Разговор на кухне",
                      "Вечером она спросила прямо и без распечатки: «Гена, что у тебя по вторникам?» Он покраснел, как на первом "
                      "свидании, и признался: вокал, полгода, хотел спеть на годовщине. Сюрприза не вышло. В субботу он пел дома "
                      "на кухне, она знала каждую ноту заранее и всё равно плакала. Тамаду отменили." + tail, 500 + bonus)
    if plan == "terms":
        return Ending("terms", "Песня под яблоней",
                      "Людмила сказала ему одно: «Что бы ты ни готовил на субботу — без ресторана, без тамады и без твоей мамы "
                      "в первом ряду». Гена два дня ходил задумчивый и отменял банкет. В субботу он спел ей на даче под яблоней, "
                      "одной. Фальшиво, с чувством, все три куплета. Алла Борисовна подыгрывала из кустов на аккордеоне." + tail,
                      1500 + bonus)
    if seen:
        return Ending("restaurant", "Ресторан и сорок телефонов",
                      "Людмила дотерпела. В субботу посреди ресторана, между горячим и тамадой, Гена встал и запел «Милая, ты услышь "
                      "меня». Сорок гостей снимали на телефон. Она хотела провалиться сквозь пол, но вспомнила слова мадам и досидела "
                      "до третьего куплета. Дома сказала: «Красиво. Но в следующий раз спроси меня»." + tail, 500 + bonus)
    return Ending("restaurant", "Ресторан и сорок телефонов",
                  "Людмила дотерпела. В субботу посреди ресторана, между горячим и тамадой, Гена встал и запел «Милая, ты услышь меня». "
                  "Сорок гостей снимали на телефон. Она улыбалась так, как улыбаются на паспорт. «Опять за меня решили, как мне "
                  "радоваться», — сказала она дома. Свекровь сказала, что лучше б изменял." + tail, bonus)


ENDINGS = ("scandal", "talk", "terms", "restaurant", "nothing")


def _finish(session: Seance, key: str, now: float, events: list[DisplayEvent], metrics: list) -> None:
    label, voice, reply, _ = FAREWELLS[key]
    session.farewell = key
    events.append(_event("voice", f"— Ты: {rules.quote(voice)}", rules.PACE_REACTION))
    _hear(session, events, reply, rules.PACE_ANSWER)
    spoken = bool(session.voiced) or key != "own"
    paid = rules._round_money(BASE_FEE * (0.3 + 0.7 * session.trust / 100)) if spoken else IDLE_FEE
    events.append(_event("line", f"💰 Людмила кладёт на стол {rules.rub(paid)}" + (" и уходит." if session.trust >= 30 else ", пересчитав дважды, и уходит.")))
    ending = resolve(session)
    session.ending, session.fee = ending.key, paid + ending.tip
    session.phase, session.finished, session.finish_reason = "done", True, "completed"
    events.append(_event("reveal", f"📜 {ending.text}", rules.PACE_REVEAL))
    metrics.append(("finish", {
        "mode": "seance", "ending": ending.key, "plan": plan_of(session), "farewell": key, "paid": paid, "tip": ending.tip,
        "fee": session.fee, "wallet": session.wallet_start + session.fee, "trust": session.trust,
        "voiced": [f"{voiced.code}:{voiced.mode}" for voiced in session.voiced], "drafts": list(session.drafts),
        "questions": list(session.asked), "methods": list(session.closed), "horo_changed": session.horo_changed,
        "moves": session.step, "duration_s": round(max(0.0, now - session.started_at), 1), "reason": "completed",
    }))


def act(session: Seance, actor_id: int, action_id: str, *, now: float) -> SeanceResult:
    """Применить действие мадам. Недопустимое — ``ValueError``, чужой — ``PermissionError``."""
    if session.finished:
        raise ValueError("сеанс уже закончен")
    if not session.is_participant(actor_id):
        raise PermissionError("действует не мадам")
    if action_id not in {action.id for action in available_actions(session)}:
        raise ValueError(f"действие недоступно: {action_id}")

    session.last_activity = now
    if action_id == "heard":
        # Перечитать — не ход: кнопки под текущим сообщением остаются рабочими.
        return SeanceResult((), (("action", {"kind": "heard", "mode": "seance", "lines": len(session.heard)}),), build_view(session), fresh=False)
    session.step += 1
    events: list[DisplayEvent] = []
    metrics: list[tuple[str, dict[str, Any]]] = []
    head, _, rest = action_id.partition(":")

    if head == "ask":
        session.phase = "ask"
    elif head == "q":
        _ask(session, rest, events, metrics)
    elif head in METHODS:
        _open_method(session, head, events)
    elif head == "p":
        session.options, session.phase = TAROT_PAIRS[rest][1], "pick"
    elif head == "a":
        _inspect(session, rest, events)
    elif head == "r":
        session.pending, session.phase = rest, "mode"
    elif head == "v":
        _voice(session, rest, events, metrics)
    elif head == "bye":
        session.phase = "farewell"
        events.append(_event("line", BYE_LINE, rules.PACE_REACTION))
    elif head == "f":
        _finish(session, rest, now, events, metrics)
    elif head == "back":
        if session.phase == "mode":
            session.pending, session.phase = None, "pick"
        elif session.phase == "pick":
            session.options, session.phase = (), "tarot"
        else:
            session.phase = "hub"
    return SeanceResult(tuple(events), tuple(metrics), build_view(session), fresh=bool(events))


# --- что на экране ----------------------------------------------------------

def _screen(session: Seance) -> str:
    """Текст текущего шага: он остаётся на экране, пока мадам выбирает."""
    phase = session.phase
    if phase == "tarot":
        return TAROT_TEXT
    if phase == "palm":
        seen = [AREAS[key][1] for key in session.areas]
        return "\n".join((PALM_TEXT, *(f"🔎 {line}" for line in seen)))
    if phase == "pick":
        marks = ("А", "Б", "В")
        if session.method == "palm":
            head = "\n".join(f"🔎 {AREAS[key][1]}" for key in session.areas)
        elif session.method == "tarot":
            head = "🃏 " + next(label for label, codes in TAROT_PAIRS.values() if codes == session.options)
        else:
            head = HORO_TEXT
        parts = [head]
        for index, code in enumerate(session.options):
            reading = READINGS[code]
            if session.method == "palm":
                parts.append(f"{marks[index]}. «{reading.voice}».")
            else:
                parts.append(f"{marks[index]}. {reading.link}\n«{reading.voice}».")
        return "\n\n".join(parts)
    if phase == "mode" and session.pending is not None:
        reading = READINGS[session.pending]
        return f"{METHOD_ICONS[reading.method]} «{reading.voice}».\n\n{MODE_HINT}"
    return ""


def _prompt(session: Seance) -> str:
    phase = session.phase
    if phase == "hub":
        return "Что дальше? Гадать не обязательно: можно сразу проводить."
    if phase == "ask":
        return f"Что уточнить? Осталось вопросов: {session.questions_left}"
    if phase == "tarot":
        return "Какие две карты связать с её рассказом?"
    if phase == "palm":
        return "Какую область осмотреть?" if not session.areas else "Что осмотреть ещё?"
    if phase == "pick":
        return "Какой путь к субботе назвать?" if session.method == "horo" else "Что ты в этом видишь?"
    if phase == "mode":
        return "Как это сказать?"
    if phase == "farewell":
        return "Что сказать на прощание?"
    return ""


def heard_text(session: Seance) -> str:
    """Всё, что сказала Людмила, по порядку. Без выводов: думать мадам придётся самой."""
    return "\n\n".join((f"📜 Что сказала {CLIENT}", *session.heard))


def build_view(session: Seance) -> SeanceView:
    return SeanceView(
        session_id=session.session_id,
        step=session.step,
        host=session.host.name,
        client=CLIENT,
        phase=session.phase,
        prompt=_prompt(session),
        screen=_screen(session),
        actions=available_actions(session),
        finished=session.finished,
        finish_reason=session.finish_reason,
        fee=session.fee,
        wallet=session.wallet_start + session.fee,
        ending=session.ending,
        ending_title=resolve(session).title if session.finished and session.ending else None,
        voiced=len(session.voiced),
    )


def start_data(session: Seance) -> dict[str, Any]:
    return {"mode": "seance", "client": "lyudmila", "items": list(session.items)}


def abandon_summary(session: Seance, reason: str, now: float) -> dict[str, Any]:
    idle_s = round(max(0.0, now - session.last_activity), 1)
    if not session.finished:
        session.finished, session.finish_reason = True, reason
    return {
        "mode": "seance", "reason": reason, "phase": session.phase, "idle_s": idle_s,
        "voiced": [f"{voiced.code}:{voiced.mode}" for voiced in session.voiced], "questions": list(session.asked),
        "methods": list(session.closed), "moves": session.step, "duration_s": round(max(0.0, now - session.started_at), 1),
    }

