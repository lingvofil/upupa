from datetime import date
from pathlib import Path

from AI import tutu, tutu_parsing


ROOT = Path(__file__).resolve().parents[1]


def test_exact_date_range_command_contract():
    params = tutu_parsing.parse_search_command(
        "билеты сочи 18.05.26-25.05.26"
    )

    assert params == {
        "origins": [{"name": "москва"}],
        "destinations": [{"name": "сочи"}],
        "departure_date": "2026-05-18",
        "return_date": "2026-05-25",
        "month": None,
        "passengers": 1,
    }


def test_date_variants_keep_round_trip_distance():
    assert tutu_parsing.generate_date_variants(
        "2026-05-18",
        "2026-05-25",
    ) == [
        ("2026-05-17", "2026-05-24"),
        ("2026-05-18", "2026-05-25"),
        ("2026-05-19", "2026-05-26"),
    ]


def test_offer_meta_marks_exact_and_alternative_dates():
    exact = tutu_parsing.build_offer_meta(
        "2026-05-18",
        "2026-05-25",
        "2026-05-18",
        "2026-05-25",
    )
    alternative = tutu_parsing.build_offer_meta(
        "2026-05-17",
        "2026-05-24",
        "2026-05-18",
        "2026-05-25",
    )

    assert exact == {
        "out_date": date(2026, 5, 18),
        "return_date": date(2026, 5, 25),
        "date_type": "exact",
        "date_shift": 0,
    }
    assert alternative == {
        "out_date": date(2026, 5, 17),
        "return_date": date(2026, 5, 24),
        "date_type": "alternative",
        "date_shift": -1,
    }


def test_tutu_runtime_uses_and_reexports_parsing_layer():
    source = (ROOT / "AI" / "tutu.py").read_text(encoding="utf-8")

    assert "from AI.tutu_parsing import (" in source
    assert "def parse_search_command(" not in source
    assert "def generate_date_variants(" not in source
    assert tutu.parse_search_command is tutu_parsing.parse_search_command
    assert tutu.generate_date_variants is tutu_parsing.generate_date_variants
