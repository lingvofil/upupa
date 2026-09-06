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

WORD_POOLS = {
    "easy": (
        "Ананас", "Акула", "Арбуз", "Бабочка", "Барабан", "Бегемот", "Бинокль", "Ботинок",
        "Бочка", "Будильник", "Букет", "Бутылка", "Велосипед", "Веник", "Верблюд", "Вертолет",
        "Вилка", "Виноград", "Волк", "Ворона", "Гвоздь", "Гитара", "Гриб", "Груша", "Дельфин",
        "Диван", "Динозавр", "Дракон", "Дрель", "Енот", "Ёж", "Жираф", "Зонт", "Кабан", "Камень",
        "Капуста", "Карандаш", "Кастрюля", "Корабль", "Корова", "Кошка", "Крокодил", "Кружка",
        "Лампа", "Лимон", "Ложка", "Лопата", "Лошадь", "Маяк", "Медведь", "Микрофон", "Молоток",
        "Морковь", "Мотоцикл", "Мышь", "Мяч", "Парашют", "Пирамида", "Пластилин", "Фонтан", "Якорь",
    ),
    "medium": (
        "Автопортрет", "Альпинист", "Арбалет", "Аэробика", "Батискаф", "Бессонница", "Бумеранг",
        "Вакуум", "Вигвам", "Витраж", "Водолаз", "Вспышка", "Гардероб", "Гербарий", "Гипноз",
        "Головоломка", "Голограмма", "Дегустатор", "Дирижер", "Домовой", "Дрессировщик", "Дровосек",
        "Дуэль", "Жонглер", "Заложник", "Затмение", "Зодиак", "Извержение", "Изобретатель",
        "Иллюзионист", "Инопланетянин", "Инъекция", "Истребитель", "Камуфляж", "Капкан", "Карнавал",
        "Каскадер", "Катамаран", "Катапульта", "Кладоискатель", "Кларнет", "Кокон", "Колибри",
        "Кольчуга", "Комбайн", "Кондуктор", "Коралл", "Костыль", "Кошмар", "Крендель", "Крепость",
        "Кристалл", "Кувалда", "Лабиринт", "Лавина", "Линза", "Локомотив", "Маскарад", "Медальон",
        "Мельница", "Микроскоп", "Мираж", "Мозаика", "Муравейник", "Небоскреб", "Невесомость",
        "Океанариум", "Орбита", "Оркестр", "Палитра", "Панорама", "Патруль", "Пейзаж", "Перископ",
        "Портал", "Призма", "Прожектор", "Радар", "Саркофаг", "Силуэт", "Симфония", "Скафандр",
        "Скульптор", "Спираль", "Сфинкс", "Талисман", "Телескоп", "Торнадо", "Трафарет", "Турбина",
        "Хамелеон", "Цунами", "Шахматист", "Экватор",
    ),
    "hard": (
        "Акупунктура", "Асфальтоукладчик", "Антиквариат", "Благотворительность", "Гравитация",
        "Громоотвод", "Дежавю", "Дефицит", "Запонки", "Кавалерия", "Кавычки", "Каланча",
        "Каллиграфия", "Камея", "Кандидат", "Кафедра", "Киберпанк", "Ключница", "Кобура", "Ковчег",
        "Код", "Колыбельная", "Командор", "Коронация", "Коррида", "Кружево", "Кукловод", "Лампада",
        "Ландшафт", "Легенда", "Лектор", "Лепнина", "Летопись", "Лоцман", "Магистраль", "Майолика",
        "Манускрипт", "Метаморфоза", "Молекула", "Монумент", "Мотив", "Мундштук", "Навигация",
        "Наледь", "Наставник", "Нитроглицерин", "Ноктюрн", "Обелиск", "Огнеборец", "Опал",
        "Осциллограф", "Оттиск", "Пергамент", "Пленэр", "Полифония", "Провиант", "Пульсар",
        "Реликвия", "Репертуар", "Реставратор", "Рефлекс", "Ритуал", "Светофильтр", "Секстант",
        "Стратосфера", "Темперамент", "Тираж", "Увертюра", "Фасад", "Филигрань", "Флюгер", "Химера",
        "Циферблат", "Шифр", "Штурман", "Эмаль", "Эпиграф", "Эхолот",
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

    used = persistence._load_word_history()
    cleaned_used: list[str] = []
    seen: set[str] = set()
    for key in used:
        if key in unique_words and key not in seen:
            cleaned_used.append(key)
            seen.add(key)
    used = cleaned_used

    available = [key for key in unique_words if key not in seen]
    if not available:
        carry_count = min(
            persistence.WORD_HISTORY_CARRYOVER,
            max(0, len(unique_words) - 1),
        )
        used = used[-carry_count:] if carry_count else []
        seen = set(used)
        available = [key for key in unique_words if key not in seen]

    chosen_key = random.choice(available)
    used.append(chosen_key)
    try:
        persistence._write_word_history(used)
    except Exception:
        logging.exception(
            "[rcroc] failed to persist word history difficulty=%s path=%s",
            difficulty,
            persistence.CROCODILE_WORD_HISTORY_PATH,
        )
    return unique_words[chosen_key]
