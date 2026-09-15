from typing import Dict

from AI.leveltravel_ranking import analyze_tours_with_ai
from AI.leveltravel_search import direct_deep_search, two_phase_search


async def execute_search(params: Dict, search_type: str) -> Dict:
    """Запускает нужный режим поиска по уже разобранным параметрам."""
    if params.get("exact_dates"):
        return await direct_deep_search(
            countries=params["countries"],
            start_date=params["exact_dates"]["start"],
            adults=params["adults"],
            nights=params["nights"],
            search_type=search_type,
        )

    country = params["countries"][0]
    return await two_phase_search(
        country_code=country["code"],
        month=params.get("month"),
        adults=params["adults"],
        nights=params["nights"],
        search_type=search_type,
    )


async def rank_search_results(hotels, date_stats: Dict, params: Dict):
    """Ранжирует найденные предложения через текущую AI policy."""
    return await analyze_tours_with_ai(hotels, date_stats, params)
