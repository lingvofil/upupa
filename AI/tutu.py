# tutu.py

import asyncio

from AI.tutu_command import run_tickets_command
from AI.tutu_parsing import (
    CITY_MAPPING,
    MAX_DATE_VARIANTS,
    MONTH_MAPPING,
    build_offer_meta,
    format_full_date,
    format_short_date,
    generate_date_variants,
    generate_month_dates,
    parse_date,
    parse_date_range,
    parse_search_command,
)
from AI.tutu_presentation import format_tickets_message
from AI.tutu_provider import (
    CITY_ID_TO_NAME,
    READ_TIMEOUT,
    TUTU_API_URL,
    TUTU_AUTOCOMPLETE_URL,
    TUTU_REFERER,
    TUTU_TIMEOUT,
    fetch_offers,
    get_city_id_from_api,
    multi_destination_search,
    parse_offer,
    resolve_city_id,
    search_tickets,
)
from AI.tutu_ranking import analyze_tickets_with_ai
from core.settings import ADMIN_ID


# Keep the historical public surface while implementations live in dedicated
# parsing/provider/ranking/presentation/command modules.
_PARSING_REEXPORTS = (
    CITY_MAPPING,
    MAX_DATE_VARIANTS,
    build_offer_meta,
    format_full_date,
    parse_date,
    parse_date_range,
)
_PROVIDER_REEXPORTS = (
    TUTU_API_URL,
    TUTU_AUTOCOMPLETE_URL,
    TUTU_REFERER,
    READ_TIMEOUT,
    TUTU_TIMEOUT,
    CITY_ID_TO_NAME,
    get_city_id_from_api,
    resolve_city_id,
    fetch_offers,
    parse_offer,
    search_tickets,
)


async def process_tickets_command(message):
    """Compatibility entrypoint used by the Telegram handler."""
    return await run_tickets_command(
        message,
        admin_id=ADMIN_ID,
        month_mapping=MONTH_MAPPING,
        parse_search_command=parse_search_command,
        generate_month_dates=generate_month_dates,
        generate_date_variants=generate_date_variants,
        multi_destination_search=multi_destination_search,
        analyze_tickets_with_ai=analyze_tickets_with_ai,
        format_tickets_message=format_tickets_message,
        format_short_date=format_short_date,
        sleep=asyncio.sleep,
    )
