"""A private salon for the Mini App. Only the currently visible signs leave the engine."""
from __future__ import annotations

import random
import secrets
from copy import copy
from dataclasses import replace

from games.guru import salon, seance, seance_engine as engine
from games.guru.stories import STORIES_BY_ID


def catalog():
    return [{"id": s.id, "name": s.names[0], "line": s.told[0].lstrip("— ")}
            for s in STORIES_BY_ID.values()]


def new_visit(story):
    if story not in STORIES_BY_ID:
        raise ValueError("Такого посетителя нет.")
    return {"id": secrets.token_hex(12), "story": story, "seed": secrets.randbits(53), "moves": []}


def replay(visit):
    session = seance.new_session(session_id=visit["id"], chat_id=0, host_id=1, host_name="Мадам", now=0,
                                 story_id=visit["story"], rng=random.Random(visit["seed"]),
                                 client_name=STORIES_BY_ID[visit["story"]].names[0])
    session.free_order = True
    if visit["story"] == seance.LUDMILA:
        # Keep the established fact IDs: later readings can resonate with either answer.
        session.story = copy(session.story)
        questions = session.content.questions
        session.story.content = replace(session.content, questions=(
            engine.QuestionData("seen", "Что у вас в руке?",
                                "Распечатка звонков. Вот, во вторник — Алла Борисовна, в четверг — опять она. "
                                "На фотографии дама лет семидесяти. А я даже её фотографию увеличивала."),
            engine.QuestionData("sure", "Вы говорили с ним о том, чего боитесь?",
                                "Собралась вчера. Он только сказал: «Ты услышишь и всё поймёшь». "
                                "Я так и не решилась спросить, что именно услышу."),
            *questions[2:]))
    events = seance.arrival_events(session)
    for index, move in enumerate(visit["moves"], 1):
        events = seance.act(session, 1, move, now=index).events
    return session, events


def scene(session, events):
    view = seance.build_view(session)
    art = None
    if session.phase == "tarot" or (session.phase == "tone" and session.state.scene == "tarot"):
        art = session.content.cards[session.card].art
    lines = [event.text for event in events if event.text and event.kind != "status"]
    if session.step == 0:
        lines = [session.content.arrival, session.heard[0]]
        if session.story.id == seance.LUDMILA:
            lines = ["Людмила садится на самый край кресла. В руке сложенная вчетверо бумага.",
                     "— Можно, я сначала у вас спрошу? А то дома я уже один раз спросила."]
    if session.story.id == seance.LUDMILA:
        opening_ids = ["q:0", "q:1"]
    else:
        questions = STORIES_BY_ID[session.story.id].questions
        opening_ids = [f"q:{next(i for i, q in enumerate(questions) if q.kind == kind)}" for kind in ("check", "open")]
    return {"client": view.client, "story": view.story, "archetype": session.content.archetype,
            "phase": view.phase, "mood": session.state.mood, "art": art, "prompt": view.prompt,
            "screen": view.screen, "lines": lines, "heard": list(session.heard),
            "opening": opening_ids if session.step == 0 else [],
            "method": session.state.scene, "finishedMethods": list(session.state.done),
            "actions": [{"id": a.id, "label": a.label, "kind": a.kind} for a in view.actions if a.id != "heard"]}


def reading_cards(session):
    statement = next((st for st in session.state.statements if st.method == "tarot" and st.alive and not st.hollow), None)
    if statement is None:
        return []
    cards = []
    for key in statement.parts:
        card, symbol, reading = engine.find_reading(session.story, key)
        variants = [{"symbol": detail.title, "look": detail.look, "phrase": option.phrase}
                    for detail in card.symbols for option in detail.readings]
        cards.append({"title": card.title, "art": card.art, "symbol": symbol.title, "look": symbol.look,
                      "phrase": reading.phrase, "variants": variants})
    return cards


# The invitation is chosen from what actually happened. Replies are a second meeting.
LETTERS = {
    "accuse": ("Я отправила то сообщение. Муж ответил: «Дома поговорим». Кладу телефон рядом, а он всё греется. Можно к вам?",
               "Людмила достаёт телефон, стирает три черновика и пишет: «Давай я сначала тебя выслушаю»."),
    "reveal": ("Я ему тогда позвонила. Теперь мы оба знаем, что сюрприз раскрыт, и оба старательно изображаем удивление. Что делать?",
               "Людмила репетирует изумлённое лицо, видит себя в зеркале и первой начинает смеяться."),
    "reserve": ("Микрофон я заказала. А теперь не знаю, как начать первую строчку. Можно порепетировать у вас?",
                "Она отбивает такт каблуком. На второй строчке перестаёт смотреть на дверь."),
    "boast": ("Сестра уже накрыла стол, чтобы выслушать мою великую новость. Может, сходите со мной?",
              "Людмила набирает сестру: «Ставь две чашки. Расскажу как было»."),
    "confront": ("После нашего разговора всё высказано вслух. Только теперь до меня дошло: а ответ-то я так и не знаю. Примете?",
                 "На обороте старой записки появляется один вопрос. На этот раз посетитель оставляет под ним место для ответа."),
    "bank": ("Ваши слова уже пересказывает весь двор. Каждый добавляет что-нибудь своё. Теперь даже я не помню, как было сначала. Можно к вам?",
             "На листе остаётся первоначальная фраза. Впервые за весь день её можно прочитать в тишине."),
}

# The handwriting and the object on the doorstep belong to this particular visitor.
POSTSCRIPTS = {
    "jealous.lena": "Я теперь кладу свой телефон экраном вверх. Просто интересно, заметит ли он.",
    "jealous.bookie": "На обороте чека из отпуска написала счёт матча. До сих пор не могу выбросить.",
    "jealous.vocal": "Прилагаю кусочек той распечатки. На обороте муж записал ноты. Я сперва приняла их за номера телефонов.",
    "jealous.friend": "Пакет подруги у меня в прихожей. Забрать она его всё никак не решится.",
    "romantic.investor": "Фото яхты я оставила. Обрезала мужчину, осталось прекрасное море.",
    "romantic.ex": "Дома снова пью чай из той кружки. Надпись повернула к стене, ручку теперь держу левой.",
    "granny.petrovich": "Банку из-под огурцов можете оставить. Петрович сделал ещё шесть, соразмеряя с серьёзностью намерений.",
    "granny.grandson": "В рамке на комоде теперь два снимка. На одном он маленький. Второй пусть сам объясняет.",
    "businessman.partner": "Снял часы. Впервые удалось договорить, ни разу не проверив время.",
    "businessman.wife": "Телефон оставил в машине. Если хотите, можем поговорить целых сорок минут.",
    "skeptic.mom": "Мама спрашивает, хорошо ли я ел. Диктофон на этот вопрос ответа почему-то не записал.",
    "skeptic.believer": "Из нагрудного кармана ничего больше не выпадает. Я пришил пуговицу.",
    "romantic.platform": "Бабушка прислала плёнку. В титрах два Ильи, а опоздал на поезд, как выяснилось, один и тот же.",
    "granny.balcony": "У герани третий листок. Валентин ходит через двор его считать, хотя в бинокль тоже видно.",
    "blogger.bots": "Выключила кольцевую лампу. Представляете, лицо в зеркале осталось.",
    "blogger.capybara": "Капибара укусила штатив. Это первое, что мы с подругой не стали переснимать.",
    "blogger.donut": "У Пончика просят автограф. Подушечку для печатей купила я, лапу предоставляет он.",
    "conspiracy.fridge": "Телефон из кастрюли вынул. Супу, оказывается, всё равно, кто его слушает.",
    "conspiracy.morse": "Приложил запись стука. На обороте три расшифровки: моя, соседа и сантехника.",
    "conspiracy.pigeon": "Если на подоконник сядет голубь с зелёным пером, не гоните. Обратный адрес на нём.",
    "student.expel": "Энергетик до вас донёс запечатанным. День уже считаю удачным.",
    "student.kebab": "На чеке осталось жирное пятно, как раз там, где сумма. Можно верить в лучшее.",
    "student.crypto": "Нарисовал график настроения на тетрадном листе. Сегодня впервые не стал его продавать.",
    "tycoon.son": "Второе кресло, пожалуйста, оставьте свободным. Если сын придёт, пусть сам выберет, где сесть.",
    "tycoon.inspection": "Оба телефона молчат. Оказывается, на обычной бумаге тоже можно поставить подпись.",
    "tycoon.waitress": "Сам принёс две чашки кофе. Одну почти не расплескал.",
    "desperate.call": "Будет звонить Саша — я теперь дослушаю до конца. Даже если первые три слова хорошие.",
    "desperate.suitcase": "В чемодане нашёл место для этой открытки. Кастрюля поместилась бы только одна.",
    "desperate.cat": "Кот прошёл по письму. Обе семьи опознали след как принадлежащий исключительно их коту.",
    "jealous.detective": "Второй том доказательств похудел на сорок страниц. Там были дубликаты моих же мыслей.",
    "jealous.trash": "Взяла пакет. Если по дороге встретим рыжего пса, это ему, а не вам.",
    "skeptic.envelope": "Линейку заберу, а конверт оставлю ещё на неделю. Странно приятно чего-то не знать.",
    "skeptic.pool": "На этот раз без секундомера. Сколько надо на разговор времени, столько и надо.",
    "businessman.raf": "В конверте карточка на кофе. Если предложат укроп, понюхайте до того, как соглашаться.",
    "businessman.tender": "В школьном альбоме увидел знакомую подпись. На договоре она у него теперь совсем другая.",
    "romantic.pilot": "Оставила на фотографии один самолёт. Стало легче разглядеть, куда он вообще летит.",
    "romantic.ring": "На свитере снова шерсть двух цветов. По меньшей мере двое со мной сегодня точно согласны.",
    "granny.scam": "Кнопочный телефон зарядила. Если Денис позвонит при вас, не перебивайте: он записывает рецепт.",
    "granny.garage": "Ключи теперь на двух разных ленточках. Михалыч всё равно пробует оба.",
}


def close_visit(session, events):
    outcome = session.outcome
    incident = next((e for e in session.state.events if e.id in LETTERS), None)
    if incident:
        invitation, resolution = LETTERS[incident.id]
        memory = session.content.events[incident.id].label
    elif outcome.client_state == "content":
        invitation = "У меня с собой одна вещь с того дня. Хочу рассказать, что было дальше. У вас есть минутка?"
        resolution = "На стол ложится маленький свёрток. На бумаге написано: «Чтобы вы тоже запомнили»."
        memory = outcome.afterword
    else:
        invitation = "Всё думаю о том, что вы сказали на прощание. Тогда мне не хватило слов. Сегодня хочу договорить."
        resolution = "Долгое молчание наконец заканчивается. На этот раз рассказ дослушан до конца."
        memory = outcome.afterword
    spoken = next((st.summary for st in reversed(session.state.statements) if st.alive and not st.hollow), "")
    gift = salon.GIFTS[session.content.archetype]
    return {"id": session.session_id, "story": session.story.id, "client": session.client, "type": session.content.archetype,
            "ending": session.ending, "title": outcome.title, "text": outcome.text, "quote": outcome.quote,
            "afterword": outcome.afterword, "epilogue": seance.epilogue(outcome), "fee": session.fee,
            "events": [e.text for e in events if e.text and e.kind in ("hint", "answer")],
            "cards": reading_cards(session), "letter": invitation + "\n\n" + POSTSCRIPTS[session.story.id],
            "memory": memory, "spoken": spoken,
            "resolution": resolution, "thread": incident.id if incident else "visit",
            "reply": None, "keepsake": gift.name if outcome.client_state == "content" else None}


REPLIES = {
    "listen": ("«Расскажите, как всё было»", "Вы придвигаете второй стул. Посетитель сам выбирает, с чего начать."),
    "admit": ("«Кажется, я могла ошибиться»", "«А я всю дорогу придумываю, как вам об этом сказать. Спасибо». Кресло придвигают ближе."),
    "ask": ("«А чего теперь хотите вы?»", "Вместо очередного прогноза вы задаёте вопрос. Ответ приходит после долгой паузы: «Попробовать по-своему»."),
}


def second_visit(record, choice):
    label, opening = REPLIES[choice]
    if choice == "listen":
        ending = record["resolution"]
    elif choice == "admit":
        if record["thread"] in {"accuse", "confront", "bank", "boast"}:
            ending = "Вы вместе составляете короткое письмо тем, кого задели ваши слова. Клиент уносит черновик: отправить его решит дома."
        else:
            ending = f"В журнале появляется приписка к делу «{record['title']}». Вы даёте прочитать её вслух и меняете одно слово по просьбе гостя."
    elif record["thread"] in {"reveal", "reserve"}:
        ending = "Людмила накрывает телефон ладонью. «Сама придумаю, как его удивить. А вам потом расскажу первой»."
    else:
        ending = "На чистом листке появляются две строки: «Что зависит от меня» и «С кем хочу поговорить». Эту записку гость забирает с собой."
    return [label, opening, ending]
