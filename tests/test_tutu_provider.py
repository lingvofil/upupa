import asyncio
from unittest.mock import AsyncMock

from AI import tutu, tutu_provider


def _raw_offer():
    return {
        "offerVariants": [
            {
                "price": {
                    "value": {
                        "amount": 4_200_000,
                        "fraction": 100,
                        "currencyCode": "RUB",
                    }
                },
                "routeIds": ["route-1"],
                "fareApplicationId": "fare-1",
            }
        ],
        "_dictionary": {
            "common": {
                "routes": {"route-1": {"segmentIds": ["segment-1"]}},
                "segments": {
                    "segment-1": {
                        "departureDateTime": "2026-05-18T10:00:00",
                        "arrivalDateTime": "2026-05-18T12:00:00",
                        "durationMinutes": 120,
                        "carrier": "SU",
                    }
                },
                "carriers": {"SU": {"name": "Аэрофлот"}},
            },
            "avia": {
                "voyages": {},
                "conditions": {
                    "fare-1": {"baggage": {"included": True}}
                },
            },
        },
    }


def test_tutu_facade_reexports_provider_layer():
    assert tutu.search_tickets is tutu_provider.search_tickets
    assert tutu.multi_destination_search is tutu_provider.multi_destination_search
    assert tutu.parse_offer is tutu_provider.parse_offer
    assert tutu.fetch_offers is tutu_provider.fetch_offers


def test_parse_offer_normalizes_price_route_carrier_and_baggage():
    ticket = tutu_provider.parse_offer(_raw_offer())

    assert ticket is not None
    assert ticket["price"] == 42_000
    assert ticket["currency"] == "RUB"
    assert ticket["airline"] == "Аэрофлот"
    assert ticket["departure"] == "2026-05-18T10:00:00"
    assert ticket["arrival"] == "2026-05-18T12:00:00"
    assert ticket["duration"] == "2ч"
    assert ticket["stops"] == 0
    assert ticket["baggage"] is True
    assert ticket["trips"][0]["baggage"] is True


def test_search_tickets_composes_provider_result_with_deeplink_and_meta(monkeypatch):
    resolve_city_id = AsyncMock(side_effect=[491, 78])
    fetch_offers = AsyncMock(return_value=[{"raw": True}])

    monkeypatch.setattr(tutu_provider, "resolve_city_id", resolve_city_id)
    monkeypatch.setattr(tutu_provider, "fetch_offers", fetch_offers)
    monkeypatch.setattr(
        tutu_provider,
        "parse_offer",
        lambda offer: {
            "price": 42_000,
            "currency": "RUB",
            "airline": "Smoke Air",
            "departure": "2026-05-18T10:00:00",
            "arrival": "2026-05-18T12:00:00",
            "duration": "2ч",
            "stops": 0,
            "baggage": True,
            "deeplink": "",
            "trips": [],
        },
    )

    tickets = asyncio.run(
        tutu_provider.search_tickets(
            "москва",
            "сочи",
            "2026-05-18",
            "2026-05-25",
            1,
            "2026-05-18",
            "2026-05-25",
        )
    )

    assert resolve_city_id.await_args_list[0].args == ("москва",)
    assert resolve_city_id.await_args_list[1].args == ("сочи",)
    fetch_offers.assert_awaited_once_with(491, 78, "2026-05-18", "2026-05-25", 1)

    assert len(tickets) == 1
    ticket = tickets[0]
    assert "route[0]=491-18052026-78" in ticket["deeplink"]
    assert "route[1]=78-25052026-491" in ticket["deeplink"]
    assert ticket["meta"]["date_type"] == "exact"
    assert ticket["meta"]["date_shift"] == 0
