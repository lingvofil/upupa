"""Difficulty-specific word pools for reverse Crocodile."""

from __future__ import annotations

import logging
import random

from games import crocodile
from games import crocodile_persistence as persistence


DIFFICULTY_LEVELS = ("easy", "medium", "hard")
DIFFICULTY_LABELS = {
    "easy": "🟢 лёгкая",
    "medium": "🟡 средняя",
    "hard": "🔴 сложная",
}

# Шкала намеренно смещена вверх относительно прежнего «Кракадила наоборот»:
# даже easy состоит из предметов/образов, которые нельзя угадать по одному силуэту.
# Hard остаётся сложным, но слова всё ещё должны иметь визуальную интерпретацию.
WORD_POOLS = {
    "easy": (
        "Альпинист", "Арбалет", "Батискаф", "Бумеранг", "Вигвам", "Витраж", "Водолаз", "Гардероб",
        "Гербарий", "Дирижер", "Домовой", "Дрессировщик", "Дровосек", "Жонглер", "Затмение",
        "Иллюзионист", "Истребитель", "Камуфляж", "Капкан", "Карнавал", "Каскадер", "Катамаран",
        "Катапульта", "Кладоискатель", "Кларнет", "Кокон", "Колибри", "Кольчуга", "Комбайн",
        "Кондуктор", "Коралл", "Костыль", "Крендель", "Крепость", "Кувалда", "Лабиринт", "Лавина",
        "Линза", "Локомотив", "Маскарад", "Мельница", "Микроскоп", "Муравейник", "Небоскреб",
        "Океанариум", "Палитра", "Перископ", "Прожектор", "Радар", "Саркофаг", "Скафандр",
        "Скульптор", "Сфинкс", "Телескоп", "Торнадо", "Турбина", "Хамелеон", "Цунами",
        "Шахматист", "Экватор",
    ),
    "medium": (
        "Автопортрет", "Аэробика", "Бессонница", "Вакуум", "Вспышка", "Гипноз", "Головоломка",
        "Голограмма", "Дегустатор", "Дуэль", "Заложник", "Зодиак", "Извержение", "Изобретатель",
        "Инопланетянин", "Инъекция", "Кошмар", "Кристалл", "Медальон", "Мираж", "Мозаика",
        "Невесомость", "Орбита", "Оркестр", "Панорама", "Патруль", "Пейзаж", "Портал", "Призма",
        "Силуэт", "Симфония", "Спираль", "Талисман", "Трафарет", "Гравитация", "Громоотвод",
        "Дежавю", "Запонки", "Кавычки", "Каллиграфия", "Киберпанк", "Колыбельная", "Коронация",
        "Кружево", "Кукловод", "Ландшафт", "Легенда", "Метаморфоза", "Молекула", "Монумент",
        "Навигация", "Наледь", "Обелиск", "Оттиск", "Пульсар", "Реликвия", "Ритуал", "Стратосфера",
        "Темперамент", "Циферблат", "Шифр", "Эхолот", "Пантомима", "Маскировка", "Иллюзия",
        "Инстинкт", "Предательство", "Ревность", "Эхо", "Тень",
    ),
    "hard": (
        "Акупунктура", "Асфальтоукладчик", "Антиквариат", "Благотворительность", "Дефицит",
        "Кавалерия", "Каланча", "Камея", "Кафедра", "Кобура", "Ковчег", "Командор", "Коррида",
        "Лампада", "Лепнина", "Летопись", "Лоцман", "Магистраль", "Майолика", "Манускрипт", "Мотив",
        "Мундштук", "Наставник", "Нитроглицерин", "Ноктюрн", "Огнеборец", "Опал", "Осциллограф",
        "Пергамент", "Пленэр", "Полифония", "Провиант", "Репертуар", "Реставратор", "Рефлекс",
        "Светофильтр", "Секстант", "Тираж", "Увертюра", "Фасад", "Филигрань", "Флюгер", "Химера",
        "Штурман", "Эмаль", "Эпиграф", "Абсурд", "Алиби", "Амнистия", "Аномалия", "Апатия",
        "Банкротство", "Вердикт", "Вдохновение", "Дилемма", "Диссонанс", "Иерархия", "Инерция",
        "Компромисс", "Контраст", "Меланхолия", "Ностальгия", "Парадокс", "Предчувствие", "Резонанс",
        "Сарказм", "Симметрия", "Суеверие", "Эйфория", "Аллегория", "Интрига", "Метафора", "Озарение",
        "Пропаганда", "Утопия", "Фанатизм", "Цензура", "Хаос", "Двойник", "Заговор", "Провокация",
        "Мистика", "Репутация", "Ирония", "Паника", "Триумф",
    ),
}


def normalize_difficulty(value: str | None) -> str:
    return value if value in DIFFICULTY_LEVELS else "medium"


def difficulty_label(value: str | None) -> str:
    return DIFFICULTY_LABELS[normalize_difficulty(value)]


def pick_reverse_crocodile_word(difficulty: str) -> str:
    """Pick a non-repeating word from one difficulty pool using shared persistent history."""
    difficulty = normalize_difficulty(difficulty)
    pool = WORD_POOLS[difficulty]
    unique_words = {crocodile._normalize_guess(word): word for word in pool}

    # History is shared with regular Crocodile so a word used there is not
    # immediately recycled here. Never filter away entries from another level:
    # switching difficulty must not reset either mode's no-repeat cycle.
    history = persistence._load_word_history()
    pool_history: list[str] = []
    pool_seen: set[str] = set()
    for key in history:
        if key in unique_words and key not in pool_seen:
            pool_history.append(key)
            pool_seen.add(key)

    available = [key for key in unique_words if key not in pool_seen]
    if not available:
        carry_count = min(
            persistence.WORD_HISTORY_CARRYOVER,
            max(0, len(unique_words) - 1),
        )
        carry = pool_history[-carry_count:] if carry_count else []
        carry_set = set(carry)
        # Start a new cycle only for the exhausted difficulty. Keep history for
        # all other levels and for regular Crocodile intact.
        history = [
            key for key in history
            if key not in unique_words or key in carry_set
        ]
        pool_seen = carry_set
        available = [key for key in unique_words if key not in pool_seen]

    chosen_key = random.choice(available)
    history.append(chosen_key)
    try:
        persistence._write_word_history(history)
    except Exception:
        logging.exception(
            "[rcroc] failed to persist word history difficulty=%s path=%s",
            difficulty,
            persistence.CROCODILE_WORD_HISTORY_PATH,
        )
    return unique_words[chosen_key]
