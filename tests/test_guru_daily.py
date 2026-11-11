"""Карта дня: одна на человека в сутки, чаевые растут за дни подряд."""

from games.guru import content as C
from games.guru import daily, rules, salon, textui
from handlers.guru_salon import CAPTION_LIMIT, MAX_NAME_LENGTH

DAY = "2026-11-10"


def test_card_depends_only_on_person_and_day():
    card = daily.draw(501, DAY)
    assert card == daily.draw(501, DAY)
    assert card.card in C.TAROT_CARDS.values() and card.picks["who"] == card.card.who, "предсказание идёт от образа карты"
    assert card.picks["what"] in C.WHAT and card.picks["when"] in C.WHEN and card.advice in C.STAR_ADVICE
    assert card.prediction == rules.prediction_plain(card.picks)


def test_cards_differ_between_people_and_days():
    by_person = {daily.draw(user, DAY).card.art for user in range(60)}
    by_day = {daily.draw(501, f"2026-11-{day:02d}").card.art for day in range(1, 31)}
    assert len(by_person) > 10 and len(by_day) > 10


def test_tip_grows_with_the_streak_up_to_a_cap():
    assert [daily.tip(streak) for streak in (0, 1, 2, 3, 7, 8, 100)] == [300, 300, 400, 500, 900, 900, 900]
    assert daily.TIP_MAX < min(item.price for item in salon.ITEMS.values()), "карта не заменяет смену"


def test_caption_shows_the_tip_and_tomorrow():
    card = daily.draw(501, DAY)
    text = textui.render_daily("Зоя", card, 400, 2, 500, 700)
    assert f"«{card.card.name}»" in text and card.prediction in text and card.advice in text
    assert "400 ₽" in text and "700 ₽" in text and "Дней подряд: 2" in text and "Завтра чаевые будут 500 ₽" in text
    capped = textui.render_daily("Зоя", card, 900, 9, 900, 7000)
    assert "Дней подряд: 9" in capped and "Завтра" not in capped, "на потолке завтрашнюю сумму не обещаем"
    repeat = textui.render_daily_repeat("Зоя", card)
    assert card.card.name in repeat and "завтра" in repeat.lower()


def test_any_caption_fits_under_a_photo():
    name = "Я" * MAX_NAME_LENGTH
    longest = max(len(textui.render_daily(name, daily.draw(user, DAY), 900, 30, 900, 1234567)) for user in range(400))
    assert longest <= CAPTION_LIMIT


def test_repeat_toast_fits_a_callback_answer():
    toasts = [textui.render_daily_toast(daily.draw(user, DAY)) for user in range(400)]
    assert max(len(toast) for toast in toasts) <= 200 and all("завтра" in toast for toast in toasts)
