"""AI ranking for normalized Tutu ticket candidates."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List

from infrastructure.ai.clients import groq_ai


async def analyze_tickets_with_ai(tickets: List[Dict], params: Dict) -> List[Dict]:
    """AI-анализ билетов с рекомендациями."""
    if not tickets or len(tickets) == 0:
        return []

    candidates = tickets[:20]
    prices = [ticket["price"] for ticket in candidates]

    avg_price = int(sum(prices) / len(prices))
    min_price = min(prices)
    max_price = max(prices)

    origins = params.get("origins", [])
    destinations = params.get("destinations", [])

    origin_str = origins[0]["name"].title() if origins else "неизвестно"
    dest_str = (
        ", ".join(destination["name"].title() for destination in destinations)
        if destinations
        else "неизвестно"
    )

    departure = params.get("departure_date", "")
    return_date = params.get("return_date", "")

    date_info = f"{departure}"
    if return_date:
        date_info += f" - {return_date} (туда-обратно)"

    candidates_simplified = []
    for index, ticket in enumerate(candidates):
        candidates_simplified.append(
            {
                "index": index,
                "price": ticket["price"],
                "airline": ticket["airline"],
                "duration": ticket["duration"],
                "stops": ticket["stops"],
                "baggage": ticket["baggage"],
            }
        )

    prompt = f"""
Ты - профессиональный эксперт по авиабилетам. Проведи анализ и выбери ТОП-7 билетов.

КОНТЕКСТ ПОИСКА:
• Маршрут: {origin_str} → {dest_str}
• Даты: {date_info}
• Пассажиров: {params.get('passengers', 1)}

РЫНОЧНАЯ СТАТИСТИКА:
• Минимальная цена: {min_price:,} ₽
• Максимальная цена: {max_price:,} ₽
• Средняя цена: {avg_price:,} ₽

КАНДИДАТЫ (топ-20 билетов):
{candidates_simplified}

ЗАДАЧА:
Выбери ТОП-7 билетов по разным сценариям:
1. Минимальный бюджет (но адекватные условия)
2. Лучший баланс цена/время
3. Прямой рейс (если есть)
4. С багажом
5. Премиум авиакомпания
6-7. Дополнительные интересные варианты

КРИТЕРИИ:
• Пересадки: 0 - отлично, 1 - нормально, 2+ - плохо
• Авиакомпании: Turkish, Emirates, Qatar - премиум; Аэрофлот, S7 - надежно
• Багаж: важно для многих
• Все цены в РУБЛЯХ

ФОРМАТ ОТВЕТА:
Верни ТОЛЬКО валидный JSON массив из 7 объектов:
[
  {{
    "index": 0,
    "ai_score": 9,
    "scenario": "Минимальный бюджет",
    "reason": "S7 Airlines, 15 200 ₽. Прямой рейс 3ч 20м. Отличный вариант для налегке."
  }},
  ...
]

Поля:
• index - номер в массиве candidates (0-19)
• ai_score - оценка 1-10
• scenario - сценарий использования (2-4 слова)
• reason - комментарий (15-40 слов), конкретные факты, эмодзи приветствуются

ВАЖНО: reason должен быть информативным с цифрами и фактами!
"""

    try:
        if groq_ai:
            response = groq_ai.generate_text(prompt)
            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                ai_results = json.loads(json_match.group(0))
                final_tickets = []
                for item in ai_results:
                    index = item.get("index")
                    if (
                        index is not None
                        and isinstance(index, int)
                        and 0 <= index < len(candidates)
                    ):
                        ticket = candidates[index].copy()
                        ticket["ai_score"] = item.get("ai_score", 0)
                        ticket["scenario"] = item.get("scenario", "Выбор AI")
                        ticket["ai_reason"] = item.get("reason", "Рекомендация AI")
                        final_tickets.append(ticket)

                final_tickets.sort(
                    key=lambda item: item.get("ai_score", 0),
                    reverse=True,
                )

                if final_tickets:
                    logging.info("AI вернул %s рекомендаций", len(final_tickets))
                    return final_tickets

    except Exception as exc:
        logging.error("Ошибка AI анализа: %s", exc)

    logging.info("Использую фолбек (без AI)")

    for ticket in candidates:
        score = 10000 - ticket["price"]
        if ticket["stops"] == 0:
            score += 5000
        elif ticket["stops"] == 1:
            score += 2000

        if ticket["baggage"]:
            score += 1000

        ticket["value_score"] = score

    candidates.sort(key=lambda item: item.get("value_score", 0), reverse=True)
    return candidates[:7]
