import json
import logging
import re
from typing import Dict, List

from AI.leveltravel_parsing import SEARCH_TYPE_HOTEL
from infrastructure.ai.clients import groq_ai


DESTINATION_INFO = {
    "IN": {
        "party": True,
        "best_months": [11, 12, 1, 2, 3],
        "description": "тусовки, свобода, пляжи",
    },
    "MV": {
        "party": False,
        "best_months": [11, 12, 1, 2, 3, 4],
        "description": "романтика, релакс, океан",
    },
    "LK": {
        "party": False,
        "best_months": [12, 1, 2, 3, 4],
        "description": "природа, серфинг, культура",
    },
    "VN": {
        "party": True,
        "best_months": [11, 12, 1, 2, 3, 4],
        "description": "еда, экскурсии, море",
    },
    "TR": {
        "party": True,
        "best_months": [5, 6, 7, 8, 9, 10],
        "description": "all inclusive, сервис",
    },
    "ID": {
        "party": True,
        "best_months": [4, 5, 6, 7, 8, 9, 10],
        "description": "джунгли, серфинг, атмосфера",
    },
    "TH": {
        "party": True,
        "best_months": [11, 12, 1, 2, 3, 4],
        "description": "ночная жизнь, острова, фрукты",
    },
    "AE": {
        "party": False,
        "best_months": [10, 11, 12, 3, 4],
        "description": "небоскребы, шопинг, пляжи",
    },
    "EG": {
        "party": False,
        "best_months": [4, 5, 9, 10, 11],
        "description": "дайвинг, пустыня, история",
    },
}


async def analyze_tours_with_ai(
    hotels: Dict[str, Dict],
    date_stats: Dict,
    params: Dict,
) -> List[Dict]:
    """Глубокий AI-анализ с контекстом рынка и развернутыми комментариями."""
    if not hotels:
        return []

    tours_list = list(hotels.values())
    tours_list.sort(key=lambda x: x.get("price", 0))
    candidates = tours_list[:30]

    destination_key = params.get("country_code")
    destination_meta = DESTINATION_INFO.get(destination_key, {})

    season_info = "Неизвестный сезон"
    if params.get("month"):
        best_months = destination_meta.get("best_months", [])
        season_info = (
            "✅ Отличный сезон"
            if params["month"] in best_months
            else "⚠️ Межсезонье/возможны дожди"
        )

    prices = [t["price"] for t in candidates]
    ratings = [t["rating"] for t in candidates if t.get("rating", 0) > 0]

    avg_price = int(sum(prices) / len(prices)) if prices else 0
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0
    sorted_prices = sorted(prices)
    median_price = sorted_prices[len(sorted_prices) // 2] if sorted_prices else 0

    market_context = {
        "min_price": min(prices) if prices else 0,
        "max_price": max(prices) if prices else 0,
        "avg_price": avg_price,
        "median_price": int(median_price),
        "avg_rating": avg_rating,
        "month_min_price": date_stats.get("min_price", 0),
        "month_max_price": date_stats.get("max_price", 0),
        "month_median_price": int(date_stats.get("median_price", 0)),
    }

    for tour in candidates:
        price = tour["price"]
        rating = tour.get("rating", 0)

        tour["price_vs_min"] = (
            round((price / market_context["min_price"] - 1) * 100, 1)
            if market_context["min_price"]
            else 0
        )
        tour["price_vs_median"] = (
            round((price / market_context["median_price"] - 1) * 100, 1)
            if market_context["median_price"]
            else 0
        )
        tour["rating_vs_avg"] = (
            round(rating - market_context["avg_rating"], 1)
            if rating > 0
            else None
        )

    countries_info = params.get("countries", [])
    if countries_info:
        countries_str = ", ".join([c["name"].title() for c in countries_info])
    else:
        countries_str = params.get("country_name", "направление").title()

    search_type_str = (
        "отели" if params.get("search_type") == SEARCH_TYPE_HOTEL else "туры"
    )

    prompt = f"""
Ты — ведущий эксперт-аналитик в сфере туризма. Твоя задача: выбрать 7 лучших предложений из списка 30 кандидатов, проведя кросс-анализ данных.

### КОНТЕКСТ:
• Направления: {countries_str} | {destination_meta.get('description', '')}
• Параметры: {params['adults']} чел, {params['nights']} ночей.
• Статистика рынка: Медиана {market_context['month_median_price']}₽, Средний рейтинг {market_context['avg_rating']}.

### ДАННЫЕ ДЛЯ АНАЛИЗА (JSON):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

### АЛГОРИТМ ПРОВЕРКИ РЕПУТАЦИИ:
Для каждого выбранного варианта проведи виртуальный "cross-check":
1. Сравни `rating` (Level.Travel) с твоими внутренними данными по Booking/Google/TripAdvisor.
2. Определи "Review Density" (Плотность отзывов): Мало (<50), Средне (50-300), Много (300+).
3. **Критическая проверка**: Если рейтинг Level.Travel > 9.0, а на внешних ресурсах < 8.0 — это "High Risk".

### ТРЕБУЕМЫЕ СЦЕНАРИИ (по 1 отелю на каждый):
1. **Low Cost Hero**: Минимальная цена, но рейтинг строго выше 7.5. Без явных рисков.
2. **Value King**: Максимальный балл по формуле (Rating / Price). Идеальный баланс.
3. **Luxury Choice**: Отель 5* с внешним рейтингом > 8.5 и топовым сервисом.
4. **Hidden Gem**: Высокий рейтинг при цене ниже медианы рынка.
5. **Safe Bet**: Отель с "Много отзывов" и стабильной репутацией (совпадение всех рейтингов).
6-7. **Bonus Picks**: На твой выбор (например, лучший пляж или уникальный дизайн).

### ПРАВИЛА JSON:
- Верни ТОЛЬКО массив из 7 объектов.
- В поле `reason` пиши про выгоды и эмоции (25-40 слов).
- В поле `risk_factor` укажи статус (Low/Medium/High) и причину.
- Используй одинарные кавычки ' внутри строк.

[
  {{
    "index": number,
    "ai_score": number (1-10),
    "scenario": "Название",
    "reason": "Текст с цифрами и эмодзи ✈️",
    "external_rating": "Оценка (Источник) / Объем отзывов",
    "risk_factor": "Описание расхождений в рейтингах"
  }}
]
"""

    # Эти значения исторически вычислялись в монолите, хотя пока не входят в prompt.
    # Сохраняем их как часть поведенчески-совместимого extraction.
    _ = season_info, search_type_str

    try:
        if groq_ai:
            response = groq_ai.generate_text(prompt)

            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                ai_results = json.loads(json_match.group(0))

                final_tours = []
                for item in ai_results:
                    idx = item.get("index")
                    if (
                        idx is not None
                        and isinstance(idx, int)
                        and 0 <= idx < len(candidates)
                    ):
                        tour = candidates[idx].copy()
                        tour["ai_score"] = item.get("ai_score", 0)
                        tour["scenario"] = item.get("scenario", "Выбор AI")
                        tour["ai_reason"] = item.get("reason", "Рекомендация AI")
                        final_tours.append(tour)

                final_tours.sort(
                    key=lambda x: x.get("ai_score", 0), reverse=True
                )

                if final_tours:
                    logging.info(
                        f"AI вернул {len(final_tours)} рекомендаций"
                    )
                    return final_tours

    except Exception as e:
        logging.error(f"Ошибка AI анализа: {e}")

    logging.info("Использую фолбек (без AI)")

    good_tours = [t for t in candidates if t.get("rating", 0) >= 6.0]
    if not good_tours:
        good_tours = candidates

    for tour in good_tours:
        rating = tour.get("rating", 5.0)
        if rating > 0:
            tour["value_score"] = rating / (tour["price"] / 10000)
        else:
            tour["value_score"] = 0

    good_tours.sort(key=lambda x: x.get("value_score", 0), reverse=True)
    return good_tours[:7]
